"""Sigstore attestations of the real signed artifact in the offline channel.

These are not snapshot tests of the screen -- ``test_snapshots_attestations``
covers that -- but of what verification actually reports: the identity, the
claims of the signing certificate, the transparency log entry and the target
channel come out of a genuine Sigstore bundle and a genuine Fulcio certificate,
so asserting them is asserting that the whole chain was walked rather than that
a string was passed through.
"""

from __future__ import annotations

import asyncio
from dataclasses import fields, replace

from rattler.networking import Client
from rattler.platform import Subdir
from rattler.repo_data import PackageRecord, RepoDataRecord
from rattler.sigstore import (
    CertificateClaims,
    TrustedRoot,
    VerifiedAttestation,
    VerifiedChecks,
)

from pixi_browse.attestations import sidecar_url, verify_record
from pixi_browse.models import AttestationData, MetadataRow
from pixi_browse.rendering import (
    build_version_details_attestation_rows,
    describe_attestation_checks,
)
from pixi_browse.repodata import query_package_records
from pixi_browse.tui.version_loader import VersionDataLoader
from tests.helpers import (
    EXAMPLE_SIDECAR_URL,
    MAIN_CHANNEL,
    SIGNING_TESTS_CHANNEL,
    SIGNING_TESTS_PACKAGE,
    GatewayFactory,
    attestation_in_status,
)

SIGNED_VERSION = "0.0.22"
SIGNED_FILE_NAME = "agent-skill-conda-forge-0.0.22-h4616a5c_0.conda"
SIGNED_ATTESTATIONS_SHA256 = (
    "68ece082f42f9caf55c3e5cdf4fccced33e51da23ed16941dc393fd9158bf3ad"
)
# The archive of the previous version: signed too, but by its own sidecar.
OTHER_PACKAGE_SHA256 = (
    "0024f57c1e6aa280b52c1475a792be2e56a89de71792b3ccbf2962fec92becc4"
)
SIGNED_SIDECAR_URL = (
    f"{SIGNING_TESTS_CHANNEL}/noarch/{SIGNED_FILE_NAME}"
    f".sigs.{SIGNED_ATTESTATIONS_SHA256}"
)
SIGNED_REPOSITORY = "https://github.com/pavelzw/skill-forge"
SIGNED_WORKFLOW_PATH = "/.github/workflows/package.yml"
SIGNED_WORKFLOW = f"{SIGNED_REPOSITORY}{SIGNED_WORKFLOW_PATH}"
SIGNED_REF = "refs/heads/main"
SIGNED_IDENTITY = f"{SIGNED_WORKFLOW}@{SIGNED_REF}"
SIGNED_COMMIT = "82e4f15bc89b31c53ef82aa08def87f9b6ec4d6d"
SIGNED_RUN = f"{SIGNED_REPOSITORY}/actions/runs/36204732641/attempts/1"
SIGNED_LOG_INDEX = 2963571265
# A certificate that was not issued to a CI workload claims none of these.
_NO_CLAIMS = CertificateClaims(
    **{field.name: None for field in fields(CertificateClaims)}
)


def _record(
    make_gateway: GatewayFactory,
    channel: str,
    package_name: str,
    file_name: str | None = None,
) -> RepoDataRecord:
    """The record of ``package_name`` named ``file_name``, or any one of them."""
    records = asyncio.run(
        query_package_records(
            gateway=make_gateway(),
            channel_names=[channel],
            platforms=[Subdir("noarch")],
            package_name=package_name,
        )
    )
    if file_name is None:
        return records[0]
    return next(record for record in records if record.file_name == file_name)


def _signed_record(make_gateway: GatewayFactory) -> RepoDataRecord:
    return _record(
        make_gateway, SIGNING_TESTS_CHANNEL, SIGNING_TESTS_PACKAGE, SIGNED_FILE_NAME
    )


def _record_of_another_archive(make_gateway: GatewayFactory) -> RepoDataRecord:
    """The signed record, but carrying the digest of an archive its sidecar
    does not sign -- what a mirror serving a swapped file would hand out."""
    signed = _signed_record(make_gateway)
    package_record = PackageRecord(
        name=SIGNING_TESTS_PACKAGE,
        version=SIGNED_VERSION,
        build=signed.build,
        build_number=signed.build_number,
        subdir=signed.subdir,
        sha256=bytes.fromhex(OTHER_PACKAGE_SHA256),
        attestations_sha256=bytes.fromhex(SIGNED_ATTESTATIONS_SHA256),
    )
    return RepoDataRecord(
        package_record, signed.file_name, signed.url, SIGNING_TESTS_CHANNEL
    )


def test_signed_record_advertises_its_sidecar(make_gateway: GatewayFactory) -> None:
    """``index_fs`` copies the sidecar digest into the repodata, which is the
    only way a client learns that an artifact is signed at all."""
    record = _signed_record(make_gateway)

    assert record.attestations_sha256 is not None
    assert record.attestations_sha256.hex() == SIGNED_ATTESTATIONS_SHA256
    assert sidecar_url(record) == SIGNED_SIDECAR_URL


def test_unsigned_record_costs_no_request(
    make_gateway: GatewayFactory, rattler_client: Client
) -> None:
    """conda-forge publishes no attestations, so the record alone settles it and
    no sidecar is fetched -- the reason this is free for nearly every package."""
    record = _record(make_gateway, MAIN_CHANNEL, "six")

    assert record.attestations_sha256 is None
    assert sidecar_url(record) is None
    assert asyncio.run(verify_record(record, client=rattler_client)) == (
        AttestationData()
    )


def test_signed_record_verifies_against_the_real_bundle(
    make_gateway: GatewayFactory, rattler_client: Client
) -> None:
    """The mirrored record keeps the upstream channel URL, so CEP 27's binding
    of the signature to ``targetChannel`` holds and verification passes cleanly.

    Every value below is read off the bundle, so this pins the whole outcome the
    attestation tab is built from: which workflow of which repository signed,
    which commit it built, and where the signature was logged.
    """
    record = _signed_record(make_gateway)

    attestation = asyncio.run(
        verify_record(
            record, client=rattler_client, trusted_root=TrustedRoot.embedded()
        )
    )

    assert attestation == AttestationData(
        sidecar_url=SIGNED_SIDECAR_URL,
        sidecar_sha256=SIGNED_ATTESTATIONS_SHA256,
        attestation=VerifiedAttestation(
            index=0,
            identity=SIGNED_IDENTITY,
            issuer="https://token.actions.githubusercontent.com",
            integrated_time="2026-09-26T00:24:56Z",
            target_channel=SIGNING_TESTS_CHANNEL,
            claims=CertificateClaims(
                build_signer_uri=SIGNED_IDENTITY,
                build_signer_digest=SIGNED_COMMIT,
                runner_environment="github-hosted",
                source_repository_uri=SIGNED_REPOSITORY,
                source_repository_digest=SIGNED_COMMIT,
                source_repository_ref=SIGNED_REF,
                source_repository_identifier="1151760276",
                source_repository_owner_uri="https://github.com/pavelzw",
                source_repository_owner_identifier="29506042",
                build_config_uri=SIGNED_IDENTITY,
                build_config_digest=SIGNED_COMMIT,
                build_trigger="push",
                run_invocation_uri=SIGNED_RUN,
                source_repository_visibility_at_signing="public",
                deployment_environment="upload",
                token_subject="repo:pavelzw/skill-forge:environment:upload",
            ),
            signed_at="2026-09-26T00:24:56Z",
            log_index=SIGNED_LOG_INDEX,
            log_origin="rekor.sigstore.dev - 1193050959916656506",
            checks=VerifiedChecks(
                certificate_chain=True,
                signed_certificate_timestamp=True,
                transparency_log=True,
                inclusion_proof=True,
            ),
            warnings=[],
        ),
    )
    assert attestation.is_verified
    assert attestation.status == "verified"
    assert build_version_details_attestation_rows(attestation) == (
        ("", "[bold green]✓ Verified[/] - certificate chain, SCT, log inclusion proof"),
        ("", ""),
        (
            "Repository",
            f"[underline link='{SIGNED_REPOSITORY}']pavelzw/skill-forge[/]"
            " (public, id 1151760276)",
        ),
        (
            "Commit",
            f"[underline link='{SIGNED_REPOSITORY}/commit/{SIGNED_COMMIT}']"
            f"{SIGNED_COMMIT}[/] on {SIGNED_REF}",
        ),
        (
            "Workflow",
            f"[underline link='{SIGNED_REPOSITORY}/blob/{SIGNED_COMMIT}"
            f"{SIGNED_WORKFLOW_PATH}'].github/workflows/package.yml[/]"
            " (trigger: push)",
        ),
        ("Runner", "github-hosted"),
        ("Environment", "upload"),
        ("Build", f"[underline link='{SIGNED_RUN}']run 36204732641, attempt 1[/]"),
        ("Signed at", "2026-09-26T00:24:56Z"),
        ("", ""),
        ("Identity", SIGNED_IDENTITY),
        ("Issuer", "https://token.actions.githubusercontent.com"),
        ("Token subject", "repo:pavelzw/skill-forge:environment:upload"),
        ("Channel", SIGNING_TESTS_CHANNEL),
        (
            "Log entry",
            "[underline link='https://search.sigstore.dev/"
            f"?logIndex={SIGNED_LOG_INDEX}']"
            f"index {SIGNED_LOG_INDEX} on rekor.sigstore.dev[/]",
        ),
        (
            "Sidecar",
            f"[underline link='{SIGNED_SIDECAR_URL}']"
            f"{SIGNED_FILE_NAME}.sigs.68ece082f4…[/] (bundle 1)",
        ),
    )


def test_a_sidecar_does_not_vouch_for_another_archive(
    make_gateway: GatewayFactory, rattler_client: Client
) -> None:
    """A genuine bundle only signs the digest in its subject, so checked against
    any other archive it is rejected, however valid the rest of it is."""
    record = _record_of_another_archive(make_gateway)

    attestation = asyncio.run(
        verify_record(
            record, client=rattler_client, trusted_root=TrustedRoot.embedded()
        )
    )

    assert not attestation.is_verified
    assert attestation.status == "unverified"
    assert attestation.sidecar_url == SIGNED_SIDECAR_URL
    assert len(attestation.warnings) == 1
    assert "artifact hash does not match any subject" in attestation.warnings[0]


def test_loader_returns_archive_details_before_it_verifies(
    make_gateway: GatewayFactory, rattler_client: Client
) -> None:
    """The archive is available before external Sigstore networking finishes,
    then the verified sidecar replaces the pending state in the same cache.
    """
    record = _signed_record(make_gateway)
    loader = VersionDataLoader(
        client=rattler_client, trusted_root=TrustedRoot.embedded()
    )
    preview_key = (
        SIGNING_TESTS_PACKAGE,
        SIGNED_VERSION,
        record.build,
        record.build_number,
        record.subdir,
        record.file_name,
    )

    async def load() -> tuple[AttestationData, AttestationData]:
        artifact = await loader.load_version_artifact_data(
            SIGNING_TESTS_PACKAGE, record, preview_key=preview_key
        )
        pending = artifact.attestation
        verified = await loader.load_version_attestation(
            record, preview_key=preview_key
        )
        assert loader.artifact_data_cache[preview_key] is verified
        return pending, verified.attestation

    pending, attestation = asyncio.run(load())

    assert pending.status == "verifying"
    assert build_version_details_attestation_rows(pending) == (
        ("", "Verifying… - package details are available while checks run"),
        ("", ""),
        (
            "Sidecar",
            f"[underline link='{SIGNED_SIDECAR_URL}']"
            f"{SIGNED_FILE_NAME}.sigs.68ece082f4…[/]",
        ),
    )
    assert attestation.is_verified
    assert attestation.status == "verified"


def test_a_bare_signature_does_not_claim_more_than_it_established() -> None:
    """A bundle whose chain and transparency log entry were not verified is a
    signature and nothing more, and the verdict says which of the two it is."""
    assert build_version_details_attestation_rows(
        attestation_in_status("verified")
    ) == (
        ("", "[bold green]✓ Verified[/] - signature only"),
        ("", ""),
        (
            "Sidecar",
            f"[underline link='{EXAMPLE_SIDECAR_URL}']pkg.conda.sigs.abc[/] (bundle 1)",
        ),
    )
    assert (
        describe_attestation_checks(
            VerifiedChecks(
                certificate_chain=True,
                signed_certificate_timestamp=True,
                transparency_log=True,
                inclusion_proof=False,
            )
        )
        == "certificate chain, SCT, log inclusion promise"
    )


def _rows_of_claims(claims: CertificateClaims) -> tuple[MetadataRow, ...]:
    """The page of a verified attestation that claims ``claims``."""
    verified = attestation_in_status("verified")
    assert verified.attestation is not None
    return build_version_details_attestation_rows(
        replace(verified, attestation=replace(verified.attestation, claims=claims))
    )


def test_a_build_in_a_deployment_environment_says_so() -> None:
    """A deployment environment is the one thing the rest of the page cannot
    imply: the identity is the workflow ref, so without it an environment-gated
    build -- required reviewers, a wait timer, a branch allowlist -- reads
    exactly like an ungated one. It is therefore shown with the provenance,
    under the runner it ran on.

    The token subject is the ``sub`` of the OIDC token the certificate was
    requested with, which is what a policy matching on the subject is pinned to,
    so it sits with the identity and the issuer rather than with the provenance
    -- the signed fixture claims neither, since its workflow ran in no
    environment.
    """
    identity = (
        "https://github.com/pavelzw/skill-forge"
        "/.github/workflows/package.yml@refs/heads/main"
    )
    issuer = "https://token.actions.githubusercontent.com"
    verified = attestation_in_status("verified")
    assert verified.attestation is not None

    assert build_version_details_attestation_rows(
        replace(
            verified,
            attestation=replace(
                verified.attestation,
                identity=identity,
                issuer=issuer,
                claims=replace(
                    _NO_CLAIMS,
                    runner_environment="github-hosted",
                    deployment_environment="upload",
                    token_subject="repo:pavelzw/skill-forge:environment:upload",
                ),
            ),
        )
    ) == (
        ("", "[bold green]✓ Verified[/] - signature only"),
        ("", ""),
        ("Runner", "github-hosted"),
        ("Environment", "upload"),
        ("", ""),
        ("Identity", identity),
        ("Issuer", issuer),
        ("Token subject", "repo:pavelzw/skill-forge:environment:upload"),
        (
            "Sidecar",
            f"[underline link='{EXAMPLE_SIDECAR_URL}']pkg.conda.sigs.abc[/] (bundle 1)",
        ),
    )


def test_a_build_outside_github_is_shown_as_it_is() -> None:
    """The shortenings are GitHub's idioms: a reusable workflow from another
    repository keeps its full URI, which is the point of showing it, and a run
    that is not a GitHub Actions one is named by its URI.

    The workflow is still linked as long as it is a file on GitHub, at the ref
    it was read from when the certificate does not say which commit that was.
    """
    rows = _rows_of_claims(
        replace(
            _NO_CLAIMS,
            source_repository_uri=SIGNED_REPOSITORY,
            source_repository_ref=SIGNED_REF,
            build_config_uri=(
                f"https://github.com/other/repo{SIGNED_WORKFLOW_PATH}@{SIGNED_REF}"
            ),
            run_invocation_uri="https://ci.example.com/builds/7",
        )
    )

    assert (
        "Workflow",
        "[underline link='https://github.com/other/repo/blob/main"
        f"{SIGNED_WORKFLOW_PATH}']https://github.com/other/repo"
        f"{SIGNED_WORKFLOW_PATH}[/]",
    ) in rows
    assert (
        "Build",
        "[underline link='https://ci.example.com/builds/7']"
        "https://ci.example.com/builds/7[/]",
    ) in rows


def test_a_workflow_that_is_not_on_github_is_not_linked() -> None:
    """A build config URI is a certificate name rather than a URL -- the ``@ref``
    suffix alone makes it resolve to nothing -- so it is only linked where the
    app knows how to turn it into a page. Elsewhere it is shown verbatim, ref and
    all: no other row carries that ref here."""
    rows = _rows_of_claims(
        replace(
            _NO_CLAIMS,
            build_config_uri=(
                f"https://gitlab.com/group/project//.gitlab-ci.yml@{SIGNED_REF}"
            ),
        )
    )

    assert (
        "Workflow",
        f"https://gitlab.com/group/project//.gitlab-ci.yml@{SIGNED_REF}",
    ) in rows


def test_unsigned_attestation_says_only_that() -> None:
    """Nothing below the verdict is known for an unsigned artifact, so nothing
    below it is shown."""
    assert build_version_details_attestation_rows(AttestationData()) == (
        ("", "Unsigned - this channel advertises no attestations for the artifact"),
    )


def test_rejected_attestation_reports_every_reason() -> None:
    """A rejected artifact still shows the sidecar it was rejected from, so it
    can be inspected by hand, and one line per reason it was rejected.

    The warning label is in ``ansi_yellow`` and not in ``yellow``, which in the
    Textual markup of a detail row would be the CSS color rather than the
    terminal's -- a glaring ``#ffff00`` on a light background.
    """
    attestation = AttestationData(
        sidecar_url="https://example.com/pkg.conda.sigs.abc",
        sidecar_sha256="abc",
        warnings=("bundle 0: subject digest mismatch", "no attestation accepted"),
    )

    assert attestation.status == "unverified"
    assert build_version_details_attestation_rows(attestation) == (
        ("", "[bold red]✗ Not verified[/] - see the warnings below"),
        ("", ""),
        (
            "Sidecar",
            "[underline link='https://example.com/pkg.conda.sigs.abc']"
            "pkg.conda.sigs.abc[/]",
        ),
        ("", ""),
        ("[ansi_yellow]Warning[/]", "bundle 0: subject digest mismatch"),
        ("[ansi_yellow]Warning[/]", "no attestation accepted"),
    )
