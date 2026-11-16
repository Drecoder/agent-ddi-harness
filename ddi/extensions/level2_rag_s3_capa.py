"""Level 2 (roadmap): related past CAPA records from the vectorized CAPA graph in S3.

Upstream, CAPA journal entries are graphed in Databricks, then vectorized into an S3 vector
index. This provider will query that index with the run's scope and hand the top matches to the
EEP slice as extra context. It is a stub until the upstream graph exists: it uses whatever AWS
credentials the shell already has (the harness never configures auth) and raises until built.
"""


class Provider:
    def __init__(self, conf: dict):
        if not conf.get("bucket") or not conf.get("index"):
            raise RuntimeError("set extensions.level2_rag_s3_capa.bucket and .index")
        self.conf = conf

    def related(self, scope: dict, top_k: int) -> list:
        raise NotImplementedError("Level 2 lands with the factory's Databricks CAPA graph (roadmap)")
