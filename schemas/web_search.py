from pydantic import BaseModel, Field


class WebSearchSchema(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=300,
        description="A concise search query for current or source-backed information",
    )
