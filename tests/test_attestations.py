"""Sigstore attestations of the real signed artifact in the offline channel.

These are not snapshot tests of the screen -- ``test_snapshots_attestations``
covers that -- but of what verification actually reports: the identity, issuer,
signing time and target channel come out of a genuine Sigstore bundle and a
genuine Fulcio certificate, so asserting them is asserting that the whole chain
was walked rather than that a string was passed through.
"""

from __future__ import annotations

import asyncio

import pytest
from rattler.networking import Client
from rattler.platform import Platform
from rattler.repo_data import RepoDataRecord

from pixi_browse.attestations import sidecar_url, verify_record
from pixi_browse.models import AttestationData
from pixi_browse.rendering import (
    build_attestation_rows,
    format_version_details_attestation_lines,
)
from pixi_browse.repodata import query_package_records
from pixi_browse.tui.version_loader import VersionDataLoader
from tests.helpers import (
    MAIN_CHANNEL,
    SKILL_FORGE_CHANNEL,
    SKILL_FORGE_PACKAGE,
    GatewayFactory,
)

SIGNED_VERSION = "0.0.16"
SIGNED_FILE_NAME = "agent-skill-conda-forge-0.0.16-h4616a5c_0.conda"
SIGNED_ATTESTATIONS_SHA256 = (
    "03305411a7bb6e707ffe19f4c203dba271e52d797afb75fd1c9024c9cc0665fe"
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
    record = _record(make_gateway, SKILL_FORGE_CHANNEL, SKILL_FORGE_PACKAGE)

    assert record.file_name == SIGNED_FILE_NAME
    assert record.attestations_sha256 is not None
    assert record.attestations_sha256.hex() == SIGNED_ATTESTATIONS_SHA256
    assert sidecar_url(record) == (
        f"{SKILL_FORGE_CHANNEL}/noarch/{SIGNED_FILE_NAME}"
        f".sigs.{SIGNED_ATTESTATIONS_SHA256}"
    )


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


@pytest.mark.network
def test_signed_record_verifies_against_the_real_bundle(
    make_gateway: GatewayFactory, rattler_client: Client
) -> None:
    """The mirrored record keeps the upstream channel URL, so CEP 27's binding
    of the signature to ``targetChannel`` holds and verification passes cleanly.

    Needs the network: the Sigstore trusted root is loaded over TUF.
    """
    record = _record(make_gateway, SKILL_FORGE_CHANNEL, SKILL_FORGE_PACKAGE)

    attestation = asyncio.run(verify_record(record, client=rattler_client))

    assert attestation == AttestationData(
        status="verified",
        sidecar_url=(
            f"{SKILL_FORGE_CHANNEL}/noarch/{SIGNED_FILE_NAME}"
            f".sigs.{SIGNED_ATTESTATIONS_SHA256}"
        ),
        sidecar_sha256=SIGNED_ATTESTATIONS_SHA256,
        identity=(
            "https://github.com/pavelzw/skill-forge"
            "/.github/workflows/package.yml@refs/heads/main"
        ),
        issuer="https://token.actions.githubusercontent.com",
        integrated_time="2026-06-11T06:29:03Z",
        target_channel=SKILL_FORGE_CHANNEL,
        bundle_index=0,
        warnings=(),
    )
    assert attestation.is_verified


@pytest.mark.network
def test_loader_verifies_while_it_reads_the_archive(
    make_gateway: GatewayFactory, rattler_client: Client
) -> None:
    """The verified attestation reaches the artifact data the detail view
    renders, which is what puts it in the tab and in the prefetch cache.

    Needs the network: the Sigstore trusted root is loaded over TUF.
    """
    record = _record(make_gateway, SKILL_FORGE_CHANNEL, SKILL_FORGE_PACKAGE)
    loader = VersionDataLoader(client=rattler_client)
    artifact = asyncio.run(
        loader.load_version_artifact_data(
            SKILL_FORGE_PACKAGE,
            record,
            preview_key=(
                SKILL_FORGE_PACKAGE,
                SIGNED_VERSION,
                record.build,
                record.build_number,
                record.subdir,
                record.file_name,
            ),
        )
    )

    assert artifact.attestation.is_verified
    assert format_version_details_attestation_lines(artifact.attestation) == (
        "Status          verified",
        "Identity        https://github.com/pavelzw/skill-forge"
        "/.github/workflows/package.yml@refs/heads/main",
        "Issuer          https://token.actions.githubusercontent.com",
        "Signed at       2026-06-11T06:29:03Z",
        f"Target channel  {SKILL_FORGE_CHANNEL}",
        "Bundle          #1",
        "Sidecar         "
        f"[@click=app.open_external_url('{SKILL_FORGE_CHANNEL}/noarch/"
        f"{SIGNED_FILE_NAME}.sigs.{SIGNED_ATTESTATIONS_SHA256}')]"
        f"{SKILL_FORGE_CHANNEL}/noarch/{SIGNED_FILE_NAME}"
        f".sigs.{SIGNED_ATTESTATIONS_SHA256}[/]",
    )


def test_unsigned_attestation_rows_say_only_that() -> None:
    """Nothing below the status is known for an unsigned artifact, so nothing
    below it is shown."""
    assert build_attestation_rows(AttestationData()) == (
        (
            "Status",
            "unsigned - this channel advertises no attestations for the artifact",
        ),
    )


def test_rejected_attestation_reports_every_reason() -> None:
    """A rejected artifact still shows the sidecar it was rejected from, so it
    can be inspected by hand, and one line per reason it was rejected."""
    attestation = AttestationData(
        status="unverified",
        sidecar_url="https://example.com/pkg.conda.sigs.abc",
        sidecar_sha256="abc",
        warnings=("bundle 0: subject digest mismatch", "no attestation accepted"),
    )

    assert format_version_details_attestation_lines(attestation) == (
        "Status   not verified - see the warnings below",
        "Sidecar  [@click=app.open_external_url('https://example.com/pkg.conda.sigs.abc')]"
        "https://example.com/pkg.conda.sigs.abc[/]",
        "",
        "Warning  bundle 0: subject digest mismatch",
        "Warning  no attestation accepted",
    )
