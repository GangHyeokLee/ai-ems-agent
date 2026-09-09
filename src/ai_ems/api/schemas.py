from pydantic import BaseModel, Field

class SecurityAnalysisRequest(BaseModel):
  outage_line_id: str
  monitored_line_id: str | None = None

class ContingencyResponseRequest(BaseModel):
  outage_line_id: str
  monitored_line_id: str | None = None
  delta_mw: float = Field(default=10.0, gt=0)
  top_n: int = Field(default=3, ge=1)

