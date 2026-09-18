"""Full authority load for project creation dependencies."""

from dataclasses import dataclass

from load_knowledge import KnowledgeLoader, KnowledgeQuery


class AuthorityGapError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthorityQuery:
    project_id: str
    series_id: str
    page_types: tuple[str, ...]


class AuthorityLoader:
    def __init__(self, control_plane, cli) -> None:
        self.loader = KnowledgeLoader(control_plane, cli)

    def load(self, query: AuthorityQuery):
        bundle = self.loader.load(KnowledgeQuery(
            project_id=query.project_id,
            page_types=query.page_types,
            limit=len(query.page_types) * 2,
        ))
        found = {item.key.split("/")[-1] for item in bundle.items}
        missing = [page_type for page_type in query.page_types if page_type not in found]
        if missing:
            raise AuthorityGapError("缺少权威知识页：" + "、".join(missing))
        return bundle
