from pydantic import BaseModel, ConfigDict, field_validator

from app.models.user_type import BASE_ROLES


class UserTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    base_role: str
    write_enabled: bool


class UserTypeCreate(BaseModel):
    name: str
    base_role: str
    write_enabled: bool = True

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name is required")
        return value

    @field_validator("base_role")
    @classmethod
    def base_role_must_be_valid(cls, value: str) -> str:
        if value not in BASE_ROLES:
            raise ValueError(f"base_role must be one of {BASE_ROLES}")
        return value


class UserTypeUpdate(BaseModel):
    write_enabled: bool
