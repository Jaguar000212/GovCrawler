import datetime
import json

from shared.permissions import PERMISSIONS, ROLE_DEFAULTS
from ..tables.auth import AuditLog, Permission, Role, RolePermission, User, UserPermission, UserSession
from ...security.hashing import hash_password


class AuthMixin:
    # ── RBAC seeding ──────────────────────────────────────────────────────────

    def seed_rbac(self):
        """Idempotent upsert of the permission catalog + built-in roles from
        shared/permissions.py. Runs once at the end of Database.__init__."""
        with self._Session() as s:
            existing_perms = {p.key for p in s.query(Permission.key).all()}
            for key, description in PERMISSIONS.items():
                if key not in existing_perms:
                    s.add(Permission(key=key, description=description))
            s.commit()

            existing_roles = {r.name: r for r in s.query(Role).all()}
            for role_name, perm_keys in ROLE_DEFAULTS.items():
                role = existing_roles.get(role_name)
                if not role:
                    role = Role(name=role_name, is_system=True)
                    s.add(role)
                    s.commit()
                existing_role_perms = {
                    rp.permission_key for rp in s.query(RolePermission).filter_by(role_id=role.id).all()
                }
                for perm_key in perm_keys:
                    if perm_key not in existing_role_perms:
                        s.add(RolePermission(role_id=role.id, permission_key=perm_key))
            s.commit()

    # ── Users ─────────────────────────────────────────────────────────────────

    def create_user(
            self,
            email: str,
            password: str,
            full_name: str | None = None,
            is_admin: bool = False,
            role_name: str | None = None,
            created_by: int | None = None,
    ) -> int:
        email = email.strip().lower()
        with self._Session() as s:
            role_id = None
            if role_name:
                role = s.query(Role).filter_by(name=role_name).first()
                role_id = role.id if role else None
            user = User(
                email=email,
                password_hash=hash_password(password),
                full_name=full_name,
                is_admin=is_admin,
                role_id=role_id,
                created_by=created_by,
            )
            s.add(user)
            s.commit()
            return user.id

    def get_user_by_email(self, email: str) -> dict | None:
        with self._Session() as s:
            user = s.query(User).filter_by(email=email.strip().lower()).first()
            return self._user_to_dict(user) if user else None

    def get_user_by_id(self, user_id: int) -> dict | None:
        with self._Session() as s:
            user = s.query(User).filter_by(id=user_id).first()
            return self._user_to_dict(user) if user else None

    def list_users(self) -> list[dict]:
        with self._Session() as s:
            users = s.query(User).order_by(User.created_at).all()
            return [self._user_to_dict(u) for u in users]

    @staticmethod
    def _user_to_dict(user: User) -> dict:
        return {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "role_id": user.role_id,
            "token_version": user.token_version,
            "failed_logins": user.failed_logins,
            "locked_until": user.locked_until,
            "last_login_at": user.last_login_at,
        }

    def set_password(self, user_id: int, password: str) -> bool:
        with self._Session() as s:
            user = s.query(User).filter_by(id=user_id).first()
            if not user:
                return False
            user.password_hash = hash_password(password)
            user.token_version += 1
            s.commit()
            return True

    def set_user_active(self, user_id: int, is_active: bool) -> bool:
        with self._Session() as s:
            user = s.query(User).filter_by(id=user_id).first()
            if not user:
                return False
            user.is_active = is_active
            if not is_active:
                user.token_version += 1
            s.commit()
            return True

    def set_user_role(self, user_id: int, role_name: str | None) -> bool:
        with self._Session() as s:
            user = s.query(User).filter_by(id=user_id).first()
            if not user:
                return False
            if role_name:
                role = s.query(Role).filter_by(name=role_name).first()
                if not role:
                    return False
                user.role_id = role.id
            else:
                user.role_id = None
            s.commit()
            return True

    def record_login_success(self, user_id: int):
        with self._Session() as s:
            user = s.query(User).filter_by(id=user_id).first()
            if user:
                user.failed_logins = 0
                user.locked_until = None
                user.last_login_at = datetime.datetime.utcnow()
                s.commit()

    def record_login_failure(self, user_id: int, threshold: int, lockout_minutes: int):
        with self._Session() as s:
            user = s.query(User).filter_by(id=user_id).first()
            if not user:
                return
            user.failed_logins += 1
            if user.failed_logins >= threshold:
                user.locked_until = datetime.datetime.utcnow() + datetime.timedelta(minutes=lockout_minutes)
            s.commit()

    def resolve_effective_permissions(self, user_id: int) -> set[str]:
        with self._Session() as s:
            user = s.query(User).filter_by(id=user_id).first()
            if not user:
                return set()
            if user.is_admin:
                return set(PERMISSIONS.keys())
            perms: set[str] = set()
            if user.role_id:
                role_perms = s.query(RolePermission.permission_key).filter_by(role_id=user.role_id).all()
                perms = {p[0] for p in role_perms}
            overrides = s.query(UserPermission).filter_by(user_id=user_id).all()
            for o in overrides:
                if o.effect == "grant":
                    perms.add(o.permission_key)
                elif o.effect == "deny":
                    perms.discard(o.permission_key)
            return perms

    def get_role_name(self, role_id: int | None) -> str | None:
        if not role_id:
            return None
        with self._Session() as s:
            role = s.query(Role).filter_by(id=role_id).first()
            return role.name if role else None

    def list_roles(self) -> list[dict]:
        """Includes each role's permission-key list — the admin dashboard's
        read-only Roles view renders this against the full PERMISSIONS
        catalog (there's no create/edit-role capability, only per-user
        overrides on top of these fixed bundles)."""
        with self._Session() as s:
            roles = s.query(Role).all()
            role_perms = {
                role.id: sorted(p[0] for p in s.query(RolePermission.permission_key).filter_by(role_id=role.id).all())
                for role in roles
            }
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "is_system": r.is_system,
                    "permissions": role_perms[r.id],
                }
                for r in roles
            ]

    # ── Per-user permission overrides ────────────────────────────────────────

    def set_user_permission_override(self, user_id: int, permission_key: str, effect: str | None) -> bool:
        """`effect` is "grant"|"deny" to add/replace an override, or None to
        remove it (reverting to the role default). Returns False if the user
        doesn't exist."""
        with self._Session() as s:
            if not s.query(User.id).filter_by(id=user_id).first():
                return False
            existing = s.query(UserPermission).filter_by(user_id=user_id, permission_key=permission_key).first()
            if effect is None:
                if existing:
                    s.delete(existing)
                    s.commit()
                return True
            if existing:
                existing.effect = effect
            else:
                s.add(UserPermission(user_id=user_id, permission_key=permission_key, effect=effect))
            s.commit()
            return True

    def list_user_permission_overrides(self, user_id: int) -> list[dict]:
        with self._Session() as s:
            overrides = s.query(UserPermission).filter_by(user_id=user_id).all()
            return [{"permission_key": o.permission_key, "effect": o.effect} for o in overrides]

    # ── Sessions (refresh tokens) ─────────────────────────────────────────────

    def create_session(
            self,
            user_id: int,
            refresh_token_hash: str,
            expires_at: datetime.datetime,
            user_agent: str | None = None,
            ip: str | None = None,
    ) -> int:
        with self._Session() as s:
            session = UserSession(
                user_id=user_id,
                refresh_token_hash=refresh_token_hash,
                user_agent=user_agent,
                ip=ip,
                expires_at=expires_at,
            )
            s.add(session)
            s.commit()
            return session.id

    def get_session_by_hash(self, refresh_token_hash: str) -> dict | None:
        with self._Session() as s:
            session = s.query(UserSession).filter_by(refresh_token_hash=refresh_token_hash).first()
            if not session:
                return None
            return {
                "id": session.id,
                "user_id": session.user_id,
                "expires_at": session.expires_at,
                "revoked_at": session.revoked_at,
            }

    def rotate_session(self, session_id: int, new_refresh_token_hash: str, new_expires_at: datetime.datetime):
        with self._Session() as s:
            session = s.query(UserSession).filter_by(id=session_id).first()
            if session:
                session.refresh_token_hash = new_refresh_token_hash
                session.expires_at = new_expires_at
                session.last_used_at = datetime.datetime.utcnow()
                s.commit()

    def revoke_session(self, session_id: int):
        with self._Session() as s:
            session = s.query(UserSession).filter_by(id=session_id).first()
            if session:
                session.revoked_at = datetime.datetime.utcnow()
                s.commit()

    def revoke_session_family(self, user_id: int):
        """Reuse-detection response: revoke every session for this user."""
        with self._Session() as s:
            s.query(UserSession).filter_by(user_id=user_id, revoked_at=None).update(
                {"revoked_at": datetime.datetime.utcnow()}
            )
            s.commit()

    # ── Audit ─────────────────────────────────────────────────────────────────

    def write_audit(
            self,
            user_id: int | None,
            action: str,
            target_type: str | None = None,
            target_id: str | None = None,
            detail: dict | None = None,
            ip: str | None = None,
    ):
        with self._Session() as s:
            s.add(
                AuditLog(
                    user_id=user_id,
                    action=action,
                    target_type=target_type,
                    target_id=str(target_id) if target_id is not None else None,
                    detail=json.dumps(detail) if detail else None,
                    ip=ip,
                )
            )
            s.commit()

    def list_audit_log(
            self,
            user_id: int | None = None,
            action_prefix: str | None = None,
            date_from: datetime.datetime | None = None,
            date_to: datetime.datetime | None = None,
            page: int = 1,
            limit: int = 50,
    ) -> tuple[list[dict], int]:
        with self._Session() as s:
            q = s.query(AuditLog)
            if user_id is not None:
                q = q.filter(AuditLog.user_id == user_id)
            if action_prefix:
                q = q.filter(AuditLog.action.like(f"{action_prefix}%"))
            if date_from is not None:
                q = q.filter(AuditLog.created_at >= date_from)
            if date_to is not None:
                q = q.filter(AuditLog.created_at <= date_to)
            total = q.count()
            rows = (
                q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset((page - 1) * limit).limit(limit).all()
            )
            emails = {
                u.id: u.email
                for u in s.query(User).filter(User.id.in_({r.user_id for r in rows if r.user_id is not None})).all()
            }
            return [
                {
                    "id": r.id,
                    "user_id": r.user_id,
                    "user_email": emails.get(r.user_id),
                    "action": r.action,
                    "target_type": r.target_type,
                    "target_id": r.target_id,
                    "detail": json.loads(r.detail) if r.detail else None,
                    "ip": r.ip,
                    "created_at": r.created_at,
                }
                for r in rows
            ], total
