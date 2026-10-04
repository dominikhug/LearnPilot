from sqlmodel import Field, SQLModel

DEFAULT_USER_ID = 1


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
