from __future__ import annotations

import yaml
from rattler.networking import Client
from rattler.package import PathType, RunExportsJson
from rattler.package_streaming import PackageArchive
from rattler.repo_data import RepoDataRecord

from pixi_browse.models import (
    PackageFile,
    PackageFilePathType,
    VersionArtifactData,
    VersionPreviewKey,
)
from pixi_browse.rendering import build_version_artifact_data

from .state import AboutUrls


class VersionDataLoader:
    def __init__(self, *, client: Client) -> None:
        self._client = client
        self.archive_cache: dict[VersionPreviewKey, PackageArchive] = {}
        self.about_urls_cache: dict[VersionPreviewKey, AboutUrls] = {}
        self.paths_cache: dict[VersionPreviewKey, list[PackageFile]] = {}
        self.artifact_data_cache: dict[VersionPreviewKey, VersionArtifactData] = {}

    def clear_caches(self) -> None:
        self.archive_cache.clear()
        self.about_urls_cache.clear()
        self.paths_cache.clear()
        self.artifact_data_cache.clear()

    def restore_caches(
        self,
        *,
        archive_cache: dict[VersionPreviewKey, PackageArchive],
        about_urls_cache: dict[VersionPreviewKey, AboutUrls],
        paths_cache: dict[VersionPreviewKey, list[PackageFile]],
        artifact_data_cache: dict[VersionPreviewKey, VersionArtifactData],
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
        self, preview_key: VersionPreviewKey, archive: PackageArchive
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
        self.paths_cache[preview_key] = paths
        return paths

    async def get_package_archive(
        self, preview_key: VersionPreviewKey, url: str
    ) -> PackageArchive:
        cached = self.archive_cache.get(preview_key)
        if cached is not None:
            return cached

        archive = await PackageArchive.from_url(self._client, url)
        self.archive_cache[preview_key] = archive
        return archive

    async def get_about_urls(
        self, preview_key: VersionPreviewKey, archive: PackageArchive
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

    async def get_info_paths(self, archive: PackageArchive) -> list[str]:
        return await archive.list_files("info")

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
        info_paths = await self.get_info_paths(archive)
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
            info_paths=info_paths,
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
