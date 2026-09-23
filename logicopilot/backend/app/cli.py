import getpass
import sys

import typer

import app.db.base  # noqa: F401  (registers all models with the ORM mapper before first query)
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import SUPER_ADMIN, User

cli = typer.Typer()


def _read_password(prompt: str) -> str:
    """getpass reads raw console input on Windows, which hangs on piped/non-tty stdin.
    Fall back to a plain stdin read in that case (still never a bare CLI arg / shell history)."""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    sys.stderr.write(prompt)
    return sys.stdin.readline().rstrip("\n")


@cli.command("create-superadmin")
def create_superadmin(
    email: str = typer.Option(..., prompt=True),
    full_name: str = typer.Option(..., prompt=True),
) -> None:
    """Seed the very first Super Admin account. Nothing in the app itself can create one."""
    password = _read_password("Password: ")
    confirm = _read_password("Confirm password: ")
    if password != confirm:
        typer.echo("Passwords do not match.", err=True)
        raise typer.Exit(code=1)
    if len(password) < 8:
        typer.echo("Password must be at least 8 characters.", err=True)
        raise typer.Exit(code=1)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing is not None:
            typer.echo(f"A user with email {email!r} already exists.", err=True)
            raise typer.Exit(code=1)

        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role=SUPER_ADMIN,
            tenant_id=None,
        )
        db.add(user)
        db.commit()
        typer.echo(f"Super admin {email!r} created.")
    finally:
        db.close()


if __name__ == "__main__":
    cli()
