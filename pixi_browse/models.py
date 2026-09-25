from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rattler.package import RunExportsJson
from rattler.sigstore import VerifiedAttestation
from rattler.version import Version

ViewMode = Literal["packages", "versions", "platforms"]
VersionRowKind = Literal["back", "section", "entry", "empty"]
VersionPreviewKey = tuple[str, str, str, int, str, str]
MetadataTab = Literal["metadata", "patches", "attestation"]
DependencyTab = Literal["dependencies", "extra_depends", "constraints", "run_exports"]
FileTab = Literal["pkg", "info"]
PackageFilePathType = Literal["hardlink", "softlink", "directory"]
# Mirrors the return type of py-rattler's `FileMode.mode`, so it can be assigned
# straight from there.
PrefixReplacementMode = Literal["binary", "text", "unknown"]
AttestationStatus = Literal["unsigned", "verified", "unverified"]
MetadataRow = tuple[str, str]


@dataclass(frozen=True)
class VersionEntry:
    version: Version
    build: str
    build_number: int
    subdir: str
    file_name: str


@dataclass(frozen=True)
class VersionRow:
    kind: VersionRowKind
    subdir: str | None = None
    entry: VersionEntry | None = None


@dataclass(frozen=True)
class PackageFile:
    path: str
    size_in_bytes: int | None = None
    sha256: bytes | None = None
    no_link: bool | None = None
    path_type: PackageFilePathType | None = None
    link_target: str | None = None
    # The install prefix is baked into some files at build time and has to be
    # rewritten on install. ``None`` means the file needs no replacement, the
    # mode says whether ``info/paths.json`` asks for text or binary rewriting.
    prefix_replacement: PrefixReplacementMode | None = None

    @property
    def is_symlink(self) -> bool:
        return self.path_type == "softlink"


@dataclass(frozen=True)
class CompareRow:
    label: str
    left: str
    right: str
    changed: bool


@dataclass(frozen=True)
class RepodataPatchDiff:
    """Differences between a package's ``info/index.json`` and its repodata record.

    Channels can ship repodata patches that rewrite the metadata served by the
    channel without touching the package archive itself. Each row pairs the
    unpatched value from ``index.json`` (``left``) with the value the gateway
    returned from the channel's repodata (``right``). Only changed rows are kept.
    """

    metadata: tuple[CompareRow, ...] = ()
    dependencies: tuple[CompareRow, ...] = ()
    constraints: tuple[CompareRow, ...] = ()

    @property
    def rows(self) -> tuple[CompareRow, ...]:
        return (*self.metadata, *self.dependencies, *self.constraints)

    @property
    def change_count(self) -> int:
        return len(self.rows)

    @property
    def is_patched(self) -> bool:
        return self.change_count > 0


@dataclass(frozen=True)
class AttestationData:
    """What the Sigstore attestations of an artifact say about it.

    A channel advertises the attestations of a package through the
    ``attestations_sha256`` field of its repodata record and serves them in a
    sidecar next to the archive. Verifying one binds a signing identity to this
    exact file, and CEP 27 binds it to the channel it was published to.

    This is the sidecar the app names plus rattler's own verification result, so
    what the attestation tab shows about a signature -- its identity, the claims
    of its signing certificate, its transparency log entry, which checks were
    performed -- is read off :class:`~rattler.sigstore.VerifiedAttestation`
    rather than copied out of it.

    ``warnings`` says what kept a bundle from being accepted, one entry per
    rejected bundle plus whatever went wrong before the bundles were reached.
    They are worth showing even for a verified artifact: a sidecar can hold
    several bundles, and the rejection of one is not hidden by the acceptance of
    another.
    """

    sidecar_url: str | None = None
    sidecar_sha256: str | None = None
    #: The bundle that verified, or ``None`` if none did.
    attestation: VerifiedAttestation | None = None
    warnings: tuple[str, ...] = ()

    @property
    def status(self) -> AttestationStatus:
        """``unsigned`` when the record advertises no attestations -- which is
        decided from the record alone and costs no request -- otherwise whether
        one of the advertised bundles was accepted."""
        if self.sidecar_url is None:
            return "unsigned"
        return "verified" if self.attestation is not None else "unverified"

    @property
    def is_verified(self) -> bool:
        return self.attestation is not None


@dataclass(frozen=True)
class VersionArtifactData:
    metadata_rows: tuple[MetadataRow, ...]
    dependencies: tuple[str, ...]
    constraints: tuple[str, ...]
    extra_depends: tuple[tuple[str, tuple[str, ...]], ...] = ()
    package_url: str = ""
    file_paths: tuple[PackageFile, ...] = ()
    info_files: tuple[PackageFile, ...] = ()
    run_exports: RunExportsJson | None = None
    repository_urls: tuple[str, ...] = ()
    documentation_urls: tuple[str, ...] = ()
    homepage_urls: tuple[str, ...] = ()
    recipe_maintainers: tuple[str, ...] = ()
    provenance_remote_url: str | None = None
    provenance_sha: str | None = None
    rattler_build_version: str | None = None
    repodata_patches: RepodataPatchDiff = RepodataPatchDiff()
    attestation: AttestationData = AttestationData()


@dataclass(frozen=True)
class CompareSelection:
    package_name: str
    entry: VersionEntry


@dataclass(frozen=True)
class CompareFileRow:
    label: str
    left: str
    right: str
    changed: bool
    left_file: PackageFile | None = None
    right_file: PackageFile | None = None
    comparison_known: bool = True


@dataclass(frozen=True)
class VersionCompareData:
    left_selection: CompareSelection
    right_selection: CompareSelection
    metadata_rows: tuple[CompareRow, ...]
    dependencies: tuple[CompareRow, ...]
    constraints: tuple[CompareRow, ...]
    run_exports: tuple[CompareRow, ...]
    files: tuple[CompareFileRow, ...]
    info_files: tuple[CompareFileRow, ...] = ()
    extra_depends: tuple[CompareRow, ...] = ()
