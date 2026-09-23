"""Sigstore attestation verification, as a browser needs it.

A channel that signs its packages advertises the attestations of each one
through the ``attestations_sha256`` field of its repodata record and serves them
in a sidecar at ``<package_url>.sigs.<sha256>``: a JSON array of Sigstore
bundles, each carrying an in-toto statement whose subject digest is the
package's own SHA256 and whose CEP 27 predicate names the channel it was
published to. Rattler does the work; this module turns the outcome into the
:class:`~pixi_browse.models.AttestationData` the detail view renders.

Pixi Browse only looks at packages, so its policy never rejects one: it browses
whatever channel it is pointed at and cannot know which publisher that channel's
users trust, and an artifact whose attestation does not verify is precisely the
artifact a user wants to see the reason for. Everything is therefore reported,
and nothing is enforced.
"""

from __future__ import annotations

from rattler.networking import Client
from rattler.repo_data import RepoDataRecord
from rattler.sigstore import (
    ChannelCheck,
    TrustedRoot,
    VerificationPolicy,
    verify_attestation,
)

from pixi_browse.models import AttestationData

# The suffix a channel serves a package's attestations under, per the CEP on
# their distribution. The mutable ``.sigs`` alias is not used: clients discover
# sidecars through the repodata and always fetch the content-addressed name.
SIDECAR_SUFFIX = ".sigs"

# `warn` reports every problem instead of raising, so a record always renders.
# No publisher is configured, so any identity a bundle verifies under is
# accepted and then shown. `ChannelCheck.WARN` keeps a `targetChannel` that
# differs from where the package was fetched visible as a warning rather than
# turning it into a rejection: for a mirror that difference is expected, and
# deciding whether it is benign is the user's call, not the browser's.
BROWSE_POLICY = VerificationPolicy.warn(channel_check=ChannelCheck.WARN)


def sidecar_url(record: RepoDataRecord) -> str | None:
    """The content-addressed attestation sidecar URL of ``record``.

    ``None`` when the record advertises no attestations. This is shown even
    when verification fails, so the sidecar can be fetched and inspected by
    hand.
    """
    attestations_sha256 = record.attestations_sha256
    if attestations_sha256 is None:
        return None
    return f"{record.url}{SIDECAR_SUFFIX}.{attestations_sha256.hex()}"


async def verify_record(
    record: RepoDataRecord,
    *,
    client: Client,
    trusted_root: TrustedRoot | None = None,
) -> AttestationData:
    """Verify the attestations ``record`` advertises, reporting every outcome.

    A record without attestations is answered from the record alone, without a
    request. Everything else costs one sidecar download plus, unless
    ``trusted_root`` supplies the trust anchors, loading the production Sigstore
    trusted root from `tuf-repo-cdn.sigstore.dev` once per process.
    """
    if record.attestations_sha256 is None:
        return AttestationData()

    url = sidecar_url(record)
    digest = record.attestations_sha256.hex()
    try:
        outcome = await verify_attestation(
            record, BROWSE_POLICY, client, trusted_root=trusted_root
        )
    except Exception as exc:
        # `BROWSE_POLICY` reports rather than raises, so this is the transport
        # and the unforeseen: a sidecar the channel does not serve after all,
        # a trusted root that cannot be loaded.
        return AttestationData(
            status="unverified",
            sidecar_url=url,
            sidecar_sha256=digest,
            warnings=(str(exc),),
        )

    attestation = outcome.attestation
    warnings = tuple(outcome.warnings)
    if attestation is None:
        return AttestationData(
            status="unverified",
            sidecar_url=url,
            sidecar_sha256=digest,
            warnings=warnings,
        )
    return AttestationData(
        status="verified",
        sidecar_url=url,
        sidecar_sha256=digest,
        identity=attestation.identity,
        issuer=attestation.issuer,
        integrated_time=attestation.integrated_time,
        target_channel=attestation.target_channel,
        bundle_index=attestation.index,
        warnings=warnings,
    )
