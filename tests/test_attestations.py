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
from rattler.platform import Platform
from rattler.repo_data import RepoDataRecord
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

SIGNED_VERSION = "2.0.0"
SIGNED_FILE_NAME = "all-signed-2.0.0-h4616a5c_0.conda"
SIGNED_ATTESTATIONS_SHA256 = (
    "43c5672db92af565e09e4fb2f4dc580a122d7ff9126c81ae235518ac981bda77"
)
SIGNED_SIDECAR_URL = (
    f"{SIGNING_TESTS_CHANNEL}/noarch/{SIGNED_FILE_NAME}"
    f".sigs.{SIGNED_ATTESTATIONS_SHA256}"
)
SIGNED_REPOSITORY = "https://github.com/tdejager/signing-tests"
SIGNED_WORKFLOW_PATH = "/.github/workflows/publish.yml"
SIGNED_WORKFLOW = f"{SIGNED_REPOSITORY}{SIGNED_WORKFLOW_PATH}"
SIGNED_REF = "refs/heads/main"
SIGNED_IDENTITY = f"{SIGNED_WORKFLOW}@{SIGNED_REF}"
SIGNED_COMMIT = "58dfa6cf041bc9c1a3eae2cca669a224fd7cbf65"
SIGNED_RUN = f"{SIGNED_REPOSITORY}/actions/runs/21989532199/attempts/1"
SIGNED_LOG_INDEX = 947449296
# A certificate that was not issued to a CI workload claims none of these.
_NO_CLAIMS = CertificateClaims(
    **{field.name: None for field in fields(CertificateClaims)}
)


def _record(
    make_gateway: GatewayFactory, channel: str, package_name: str
) -> RepoDataRecord:
    records = asyncio.run(
        query_package_records(
            gateway=make_gateway(),
            channel_names=[channel],
            platforms=[Platform("noarch")],
            package_name=package_name,
        )
    )
    return records[0]


def test_signed_record_advertises_its_sidecar(make_gateway: GatewayFactory) -> None:
    """``index_fs`` copies the sidecar digest into the repodata, which is the
    only way a client learns that an artifact is signed at all."""
    record = _record(make_gateway, SIGNING_TESTS_CHANNEL, SIGNING_TESTS_PACKAGE)

    assert record.file_name == SIGNED_FILE_NAME
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
    record = _record(make_gateway, SIGNING_TESTS_CHANNEL, SIGNING_TESTS_PACKAGE)

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
            integrated_time="2026-02-13T14:00:05Z",
            target_channel=SIGNING_TESTS_CHANNEL,
            claims=CertificateClaims(
                build_signer_uri=SIGNED_IDENTITY,
                build_signer_digest=SIGNED_COMMIT,
                runner_environment="github-hosted",
                source_repository_uri=SIGNED_REPOSITORY,
                source_repository_digest=SIGNED_COMMIT,
                source_repository_ref=SIGNED_REF,
                source_repository_identifier="1156993586",
                source_repository_owner_uri="https://github.com/tdejager",
                source_repository_owner_identifier="417374",
                build_config_uri=SIGNED_IDENTITY,
                build_config_digest=SIGNED_COMMIT,
                build_trigger="workflow_dispatch",
                run_invocation_uri=SIGNED_RUN,
                source_repository_visibility_at_signing="public",
            ),
            signed_at="2026-02-13T14:00:05Z",
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


def test_loader_verifies_while_it_reads_the_archive(
    make_gateway: GatewayFactory, rattler_client: Client
) -> None:
    """The verified attestation reaches the artifact data the detail view
    renders, which is what puts it in the tab and in the prefetch cache.

    The rows below are the page a user reads: a verdict that names what was
    checked, the provenance the certificate claims, and the values an audit
    needs, with the repository, commit, run, log entry and sidecar as links.
    ``test_snapshots_attestations`` shows how they are laid out.
    """
    record = _record(make_gateway, SIGNING_TESTS_CHANNEL, SIGNING_TESTS_PACKAGE)
    loader = VersionDataLoader(
        client=rattler_client, trusted_root=TrustedRoot.embedded()
    )
    artifact = asyncio.run(
        loader.load_version_artifact_data(
            SIGNING_TESTS_PACKAGE,
            record,
            preview_key=(
                SIGNING_TESTS_PACKAGE,
                SIGNED_VERSION,
                record.build,
                record.build_number,
                record.subdir,
                record.file_name,
            ),
        )
    )

    assert artifact.attestation.is_verified
    assert build_version_details_attestation_rows(artifact.attestation) == (
        ("", "[bold green]✓ Verified[/] - certificate chain, SCT, log inclusion proof"),
        ("", ""),
        (
            "Repository",
            f"[underline link='{SIGNED_REPOSITORY}']tdejager/signing-tests[/]"
            " (public, id 1156993586)",
        ),
        (
            "Commit",
            f"[underline link='{SIGNED_REPOSITORY}/commit/{SIGNED_COMMIT}']"
            f"{SIGNED_COMMIT}[/] on {SIGNED_REF}",
        ),
        (
            "Workflow",
            f"[underline link='{SIGNED_REPOSITORY}/blob/{SIGNED_COMMIT}"
            f"{SIGNED_WORKFLOW_PATH}'].github/workflows/publish.yml[/]"
            " (trigger: workflow_dispatch)",
        ),
        ("Runner", "github-hosted"),
        ("Build", f"[underline link='{SIGNED_RUN}']run 21989532199, attempt 1[/]"),
        ("Signed at", "2026-02-13T14:00:05Z"),
        ("", ""),
        ("Identity", SIGNED_IDENTITY),
        ("Issuer", "https://token.actions.githubusercontent.com"),
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
            f"{SIGNED_FILE_NAME}.sigs.43c5672db9…[/] (bundle 1)",
        ),
    )


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
    can be inspected by hand, and one line per reason it was rejected."""
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
        ("[yellow]Warning[/]", "bundle 0: subject digest mismatch"),
        ("[yellow]Warning[/]", "no attestation accepted"),
    )
