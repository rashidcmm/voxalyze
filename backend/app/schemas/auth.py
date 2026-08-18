import uuid

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    message: str


class EmailOnlyRequest(BaseModel):
    """Shared shape for /auth/forgot-password and /auth/resend-verification —
    both endpoints take only an email and always return the same generic
    MessageResponse regardless of what they find (see the plan's Global
    Constraints: enumeration-safety resolution)."""

    email: EmailStr


class VerifyEmailRequest(BaseModel):
    token: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=72)
