from __future__ import annotations

from dataclasses import dataclass, replace

import yaml
from rattler.networking import Client
from rattler.package import PathType, RunExportsJson
from rattler.package_streaming import PackageArchive
from rattler.repo_data import RepoDataRecord

from pixi_browse.artifacts import into_package_record
from pixi_browse.models import (
    ArtifactCacheKey,
    ArtifactDescriptor,
    ArtifactSource,
    LocalArtifactSource,
    PackageFile,
    PackageFilePathType,
    VersionArtifactData,
    VersionEntry,
    VersionPreviewKey,
)
from pixi_browse.rendering import build_artifact_data, build_version_artifact_data

from .state import AboutUrls


@dataclass(frozen=True)
class LoadedArtifact:
    source: ArtifactSource
    descriptor: ArtifactDescriptor
    entry: VersionEntry
    archive: PackageArchive
    data: VersionArtifactData


class VersionDataLoader:
    def __init__(self, *, client: Client) -> None:
        self._client = client
        self.archive_cache: dict[ArtifactCacheKey, PackageArchive] = {}
        self.about_urls_cache: dict[ArtifactCacheKey, AboutUrls] = {}
        self.paths_cache: dict[ArtifactCacheKey, list[PackageFile]] = {}
        self.artifact_data_cache: dict[ArtifactCacheKey, VersionArtifactData] = {}

    def clear_caches(self) -> None:
        self.archive_cache.clear()
        self.about_urls_cache.clear()
        self.paths_cache.clear()
        self.artifact_data_cache.clear()

    def restore_caches(
        self,
        *,
        archive_cache: dict[ArtifactCacheKey, PackageArchive],
        about_urls_cache: dict[ArtifactCacheKey, AboutUrls],
        paths_cache: dict[ArtifactCacheKey, list[PackageFile]],
        artifact_data_cache: dict[ArtifactCacheKey, VersionArtifactData],
    ) -> None:
        self.archive_cache.clear()
        self.archive_cache.update(archive_cache)
        self.about_urls_cache.clear()
        self.about_urls_cache.update(about_urls_cache)
        self.paths_cache.clear()
        self.paths_cache.update(paths_cache)
        self.artifact_data_cache.clear()
        self.artifact_data_cache.update(artifact_data_cache)

    @staticmethod
    def _path_type_name(path_type: PathType) -> PackageFilePathType | None:
        if path_type.hardlink:
            return "hardlink"
        if path_type.softlink:
            return "softlink"
        if path_type.directory:
            return "directory"
        return None

    @staticmethod
    def extract_rattler_build_version(rendered_recipe_text: str) -> str | None:
        data = yaml.safe_load(rendered_recipe_text)
        if not isinstance(data, dict):
            return None

        system_tools = data.get("system_tools")
        if not isinstance(system_tools, dict):
            return None

        rattler_build_version = system_tools.get("rattler-build")
        if rattler_build_version is None:
            return None

        return str(rattler_build_version)

    async def get_package_paths(
        self, preview_key: ArtifactCacheKey, archive: PackageArchive
    ) -> list[PackageFile]:
        cached = self.paths_cache.get(preview_key)
        if cached is not None:
            return cached

        paths_json = await archive.paths_json()
        paths = [
            PackageFile(
                path=str(path.relative_path),
                size_in_bytes=path.size_in_bytes,
                sha256=path.sha256,
                no_link=path.no_link,
                path_type=self._path_type_name(path.path_type),
            )
            for path in paths_json.paths
        ]
        symlink_paths = {path.path for path in paths if path.is_symlink}
        if symlink_paths:
            link_targets: dict[str, str | None] = {}
            async for entry in archive.stream("pkg"):
                if entry.is_symlink and entry.name in symlink_paths:
                    link_targets[entry.name] = entry.link_target
                    if len(link_targets) == len(symlink_paths):
                        break
            paths = [
                replace(path, link_target=link_targets.get(path.path))
                if path.is_symlink
                else path
                for path in paths
            ]
        self.paths_cache[preview_key] = paths
        return paths

    async def get_package_archive(
        self, preview_key: ArtifactCacheKey, url: str
    ) -> PackageArchive:
        cached = self.archive_cache.get(preview_key)
        if cached is not None:
            return cached

        archive = await PackageArchive.from_url(self._client, url)
        self.archive_cache[preview_key] = archive
        return archive

    async def get_artifact_archive(self, source: ArtifactSource) -> PackageArchive:
        cached = self.archive_cache.get(source.cache_key)
        if cached is not None:
            return cached

        if isinstance(source, LocalArtifactSource):
            archive = await PackageArchive.from_path(source.path)
        else:
            archive = await PackageArchive.from_url(self._client, source.url)
        self.archive_cache[source.cache_key] = archive
        return archive

    async def get_about_urls(
        self, preview_key: ArtifactCacheKey, archive: PackageArchive
    ) -> AboutUrls:
        cached = self.about_urls_cache.get(preview_key)
        if cached is not None:
            return cached

        about_json = await archive.about_json()
        recipe_maintainers = about_json.extra.get("recipe-maintainers", [])
        if isinstance(recipe_maintainers, str):
            recipe_maintainers = [recipe_maintainers]
        elif not isinstance(recipe_maintainers, list):
            recipe_maintainers = []

        about_urls = AboutUrls(
            repository=tuple(str(url) for url in about_json.dev_url),
            documentation=tuple(str(url) for url in about_json.doc_url),
            homepage=tuple(str(url) for url in about_json.home),
            recipe_maintainers=tuple(
                str(maintainer)
                for maintainer in recipe_maintainers
                if isinstance(maintainer, str)
            ),
            provenance_remote_url=(
                str(about_json.extra.get("remote_url"))
                if about_json.extra.get("remote_url")
                else None
            ),
            provenance_sha=(
                str(about_json.extra.get("sha"))
                if about_json.extra.get("sha")
                else None
            ),
        )
        try:
            rendered_recipe_bytes = await archive.read_file(
                "info/recipe/rendered_recipe.yaml"
            )
            if rendered_recipe_bytes is None:
                raise FileNotFoundError(
                    "package does not contain info/recipe/rendered_recipe.yaml"
                )
            about_urls = AboutUrls(
                repository=about_urls.repository,
                documentation=about_urls.documentation,
                homepage=about_urls.homepage,
                recipe_maintainers=about_urls.recipe_maintainers,
                provenance_remote_url=about_urls.provenance_remote_url,
                provenance_sha=about_urls.provenance_sha,
                rattler_build_version=self.extract_rattler_build_version(
                    rendered_recipe_bytes.decode("utf-8", errors="replace")
                ),
            )
        except Exception:
            pass

        self.about_urls_cache[preview_key] = about_urls
        return about_urls

    async def get_info_files(self, archive: PackageArchive) -> list[PackageFile]:
        files: list[PackageFile] = []
        async for entry in archive.stream("info"):
            if entry.is_file:
                files.append(PackageFile(path=entry.name, size_in_bytes=entry.size))
            elif entry.is_symlink:
                files.append(
                    PackageFile(
                        path=entry.name,
                        size_in_bytes=entry.size,
                        path_type="softlink",
                        link_target=entry.link_target,
                    )
                )
        return files

    async def get_run_exports(self, archive: PackageArchive) -> RunExportsJson | None:
        return await archive.run_exports_json()

    async def load_version_details(
        self,
        package_name: str,
        record: RepoDataRecord,
        *,
        preview_key: VersionPreviewKey,
    ) -> VersionArtifactData:
        return await self.load_version_artifact_data(
            package_name,
            record,
            preview_key=preview_key,
        )

    async def load_version_artifact_data(
        self,
        package_name: str,
        record: RepoDataRecord,
        *,
        preview_key: VersionPreviewKey,
    ) -> VersionArtifactData:
        cached = self.artifact_data_cache.get(preview_key)
        if cached is not None:
            return cached

        archive = await self.get_package_archive(preview_key, str(record.url))
        package_paths = await self.get_package_paths(preview_key, archive)
        info_files = await self.get_info_files(archive)
        about_urls = AboutUrls()
        run_exports: RunExportsJson | None = None

        # TODO: clean up once https://github.com/conda/rattler/issues/2349 is fixed.
        try:
            about_urls = await self.get_about_urls(preview_key, archive)
        except Exception:
            pass

        try:
            run_exports = await self.get_run_exports(archive)
        except Exception:
            pass

        artifact_data = build_version_artifact_data(
            package_name,
            record,
            package_paths=package_paths,
            info_files=info_files,
            repository_urls=about_urls.repository,
            documentation_urls=about_urls.documentation,
            homepage_urls=about_urls.homepage,
            recipe_maintainers=about_urls.recipe_maintainers,
            provenance_remote_url=about_urls.provenance_remote_url,
            provenance_sha=about_urls.provenance_sha,
            rattler_build_version=about_urls.rattler_build_version,
            run_exports=run_exports,
        )
        self.artifact_data_cache[preview_key] = artifact_data
        return artifact_data

    async def load_artifact_source(self, source: ArtifactSource) -> LoadedArtifact:
        archive = await self.get_artifact_archive(source)
        index = await archive.index_json()
        if isinstance(source, LocalArtifactSource):
            file_name = source.path.name
            size = source.path.stat().st_size
        else:
            file_name = source.display_name
            size = None
        if not file_name:
            file_name = f"{index.name.source}-{index.version}-{index.build}.conda"

        record = into_package_record(index)
        if size is not None:
            record.size = size
        descriptor = ArtifactDescriptor(
            record=record,
            file_name=file_name,
            source=source,
        )
        entry = VersionEntry(
            version=record.version,
            build=record.build,
            build_number=record.build_number,
            subdir=record.subdir,
            file_name=descriptor.file_name,
        )

        cached = self.artifact_data_cache.get(source.cache_key)
        if cached is not None:
            return LoadedArtifact(source, descriptor, entry, archive, cached)

        package_paths = await self.get_package_paths(source.cache_key, archive)
        info_files = await self.get_info_files(archive)
        about_urls = AboutUrls()
        run_exports: RunExportsJson | None = None
        try:
            about_urls = await self.get_about_urls(source.cache_key, archive)
        except Exception:
            pass
        try:
            run_exports = await self.get_run_exports(archive)
        except Exception:
            pass

        artifact_data = build_artifact_data(
            descriptor,
            package_paths=package_paths,
            info_files=info_files,
            repository_urls=about_urls.repository,
            documentation_urls=about_urls.documentation,
            homepage_urls=about_urls.homepage,
            recipe_maintainers=about_urls.recipe_maintainers,
            provenance_remote_url=about_urls.provenance_remote_url,
            provenance_sha=about_urls.provenance_sha,
            rattler_build_version=about_urls.rattler_build_version,
            run_exports=run_exports,
        )
        self.artifact_data_cache[source.cache_key] = artifact_data
        return LoadedArtifact(source, descriptor, entry, archive, artifact_data)
