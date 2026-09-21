"""Full authority load for project creation dependencies."""

from dataclasses import dataclass

from load_knowledge import KnowledgeLoader, KnowledgeQuery, bundle_to_dict


class AuthorityGapError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthorityQuery:
    project_id: str
    series_id: str
    page_types: tuple[str, ...]


class AuthorityLoader:
    def __init__(self, control_plane, cli, cache_store=None,
                 cached_bundle=None) -> None:
        self.loader = KnowledgeLoader(
            control_plane, cli,
            cached_bundle=cached_bundle,
        )
        self.cache_store = cache_store

    def load(self, query: AuthorityQuery, allow_offline_cache: bool = False):
        bundle = self.loader.load(KnowledgeQuery(
            project_id=query.project_id,
            series_id=query.series_id,
            page_types=query.page_types,
            limit=len(query.page_types) * 2,
        ), allow_offline_cache=allow_offline_cache)
        found = {item.key.split("/")[-1] for item in bundle.items}
        missing = [page_type for page_type in query.page_types if page_type not in found]
        if missing:
            raise AuthorityGapError("缺少权威知识页：" + "、".join(missing))
        if not bundle.offline and self.cache_store is not None:
            self.cache_store.save(bundle_to_dict(bundle))
        return bundle
