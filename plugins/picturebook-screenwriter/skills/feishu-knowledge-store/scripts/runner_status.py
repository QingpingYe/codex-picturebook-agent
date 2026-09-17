"""Bootstrap and run states for the Feishu sync runner."""


class BootstrapState:
    REQUIRED = "bootstrap_required"
    IN_PROGRESS = "bootstrap_in_progress"
    COMPLETE = "bootstrap_complete"
    FAILED = "bootstrap_failed"


class RunStatus:
    PREPARED = "prepared"
    PUBLISHED = "published"
    VERIFIED = "verified"
    FAILED = "failed"
