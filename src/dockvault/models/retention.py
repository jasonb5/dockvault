from pydantic import BaseModel


class RetentionConfig(BaseModel):
    enabled: bool | None = None
    keep_last: int | None = None
    keep_daily: int | None = None
    keep_weekly: int | None = None
    keep_monthly: int | None = None
    keep_yearly: int | None = None

    def has_options(self) -> bool:
        return any(
            value is not None
            for value in (
                self.keep_last,
                self.keep_daily,
                self.keep_weekly,
                self.keep_monthly,
                self.keep_yearly,
            )
        )
