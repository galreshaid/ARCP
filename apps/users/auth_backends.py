from __future__ import annotations

import importlib.util
import re
from typing import Any

from django.conf import settings
from django.contrib.auth.backends import BaseBackend, ModelBackend
from django.db.models import Q

from apps.users.models import User


class LocalEmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        login_value = (username or kwargs.get("email") or "").strip()
        if not login_value or not password:
            return None

        user = User.objects.filter(
            Q(email__iexact=login_value) | Q(username__iexact=login_value)
        ).first()
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None


class OptionalLDAPBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        login_value = (username or kwargs.get("email") or "").strip()
        if not self._can_use_ldap() or not login_value or not password:
            return None

        ldap_profile = self._authenticate_against_directory(login_value, password)
        if not ldap_profile:
            return None

        user = self._get_or_build_user(login_value, ldap_profile)
        if user and self.user_can_authenticate(user):
            return user
        return None

    def get_user(self, user_id):
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None

    def user_can_authenticate(self, user):
        is_active = getattr(user, "is_active", None)
        return is_active or is_active is None

    def _can_use_ldap(self):
        return (
            bool(getattr(settings, "LDAP_AUTH_ENABLED", False))
            and bool(getattr(settings, "LDAP_SERVER_URI", ""))
            and bool(getattr(settings, "LDAP_USER_SEARCH_BASE", ""))
            and (
                importlib.util.find_spec("ldap3") is not None
                or importlib.util.find_spec("ldap") is not None
            )
        )

    def _authenticate_against_directory(self, login_value: str, password: str):
        if importlib.util.find_spec("ldap3") is not None:
            return self._authenticate_with_ldap3(login_value, password)
        if importlib.util.find_spec("ldap") is not None:
            return self._authenticate_with_python_ldap(login_value, password)
        return None

    def _authenticate_with_ldap3(self, login_value: str, password: str):
        from ldap3 import ALL, Connection, Server, SUBTREE, Tls

        server = Server(getattr(settings, "LDAP_SERVER_URI", ""), get_info=ALL, tls=Tls())
        conn = Connection(
            server,
            user=getattr(settings, "LDAP_BIND_DN", "") or None,
            password=getattr(settings, "LDAP_BIND_PASSWORD", "") or None,
            auto_bind=False,
        )

        if not conn.bind():
            return None

        try:
            if getattr(settings, "LDAP_START_TLS", False):
                conn.start_tls()

            search_filter = self._ldap_search_filter(login_value)
            if not conn.search(
                search_base=getattr(settings, "LDAP_USER_SEARCH_BASE", ""),
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=self._requested_attributes(),
            ):
                return None

            if not conn.entries:
                return None

            entry = conn.entries[0]
            user_dn = entry.entry_dn
            attrs = self._extract_ldap3_attributes(entry)
        finally:
            conn.unbind()

        user_conn = Connection(server, user=user_dn, password=password, auto_bind=False)
        if not user_conn.bind():
            return None
        user_conn.unbind()

        return {
            "dn": user_dn,
            "username": attrs.get(getattr(settings, "LDAP_LOGIN_ATTRIBUTE", "sAMAccountName"), ""),
            "email": attrs.get(getattr(settings, "LDAP_EMAIL_ATTRIBUTE", "mail"), ""),
            "first_name": attrs.get(getattr(settings, "LDAP_FIRST_NAME_ATTRIBUTE", "givenName"), ""),
            "last_name": attrs.get(getattr(settings, "LDAP_LAST_NAME_ATTRIBUTE", "sn"), ""),
        }

    def _authenticate_with_python_ldap(self, login_value: str, password: str):
        import ldap
        from ldap.filter import escape_filter_chars

        conn = ldap.initialize(getattr(settings, "LDAP_SERVER_URI", ""))
        conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
        if getattr(settings, "LDAP_START_TLS", False):
            conn.start_tls_s()

        bind_dn = getattr(settings, "LDAP_BIND_DN", "")
        bind_password = getattr(settings, "LDAP_BIND_PASSWORD", "")
        if bind_dn:
            conn.simple_bind_s(bind_dn, bind_password)
        else:
            conn.simple_bind_s()

        search_attr = getattr(settings, "LDAP_LOGIN_ATTRIBUTE", "sAMAccountName")
        results = conn.search_s(
            getattr(settings, "LDAP_USER_SEARCH_BASE", ""),
            ldap.SCOPE_SUBTREE,
            f"({search_attr}={escape_filter_chars(login_value)})",
            self._requested_attributes(),
        )
        conn.unbind_s()

        if not results:
            return None

        user_dn, raw_attrs = results[0]
        if not user_dn:
            return None

        verify_conn = ldap.initialize(getattr(settings, "LDAP_SERVER_URI", ""))
        verify_conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
        if getattr(settings, "LDAP_START_TLS", False):
            verify_conn.start_tls_s()
        verify_conn.simple_bind_s(user_dn, password)
        verify_conn.unbind_s()

        attrs = {
            key: self._decode_ldap_value(values[0] if values else b"")
            for key, values in (raw_attrs or {}).items()
        }
        return {
            "dn": user_dn,
            "username": attrs.get(getattr(settings, "LDAP_LOGIN_ATTRIBUTE", "sAMAccountName"), ""),
            "email": attrs.get(getattr(settings, "LDAP_EMAIL_ATTRIBUTE", "mail"), ""),
            "first_name": attrs.get(getattr(settings, "LDAP_FIRST_NAME_ATTRIBUTE", "givenName"), ""),
            "last_name": attrs.get(getattr(settings, "LDAP_LAST_NAME_ATTRIBUTE", "sn"), ""),
        }

    def _ldap_search_filter(self, login_value: str):
        template = getattr(settings, "LDAP_USER_SEARCH_FILTER", "") or "(sAMAccountName=%(user)s)"
        return template % {"user": self._escape_filter_value(login_value)}

    def _escape_filter_value(self, value: str):
        escaped = str(value or "")
        replacements = {
            "\\": r"\5c",
            "*": r"\2a",
            "(": r"\28",
            ")": r"\29",
            "\x00": r"\00",
        }
        for needle, replacement in replacements.items():
            escaped = escaped.replace(needle, replacement)
        return escaped

    def _requested_attributes(self):
        attrs = {
            getattr(settings, "LDAP_LOGIN_ATTRIBUTE", "sAMAccountName"),
            getattr(settings, "LDAP_EMAIL_ATTRIBUTE", "mail"),
            getattr(settings, "LDAP_FIRST_NAME_ATTRIBUTE", "givenName"),
            getattr(settings, "LDAP_LAST_NAME_ATTRIBUTE", "sn"),
        }
        return [item for item in attrs if item]

    def _extract_ldap3_attributes(self, entry):
        values: dict[str, str] = {}
        for attr_name in self._requested_attributes():
            try:
                raw_value = entry[attr_name].value
            except Exception:
                raw_value = ""
            if isinstance(raw_value, list):
                raw_value = raw_value[0] if raw_value else ""
            values[attr_name] = str(raw_value or "").strip()
        return values

    def _decode_ldap_value(self, value: Any):
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore").strip()
        return str(value or "").strip()

    def _get_or_build_user(self, login_value: str, ldap_profile: dict[str, str]):
        username = (ldap_profile.get("username") or login_value or "").strip()
        email = (ldap_profile.get("email") or "").strip()
        fallback_email = self._fallback_email(username)
        resolved_email = email or fallback_email

        user = User.objects.filter(
            Q(username__iexact=username) | Q(email__iexact=resolved_email)
        ).first()

        if user is None:
            base_username = username[:150] or resolved_email.split("@", 1)[0][:150] or "ldap_user"
            user = User(
                username=base_username,
                email=resolved_email,
                first_name=(ldap_profile.get("first_name") or "").strip(),
                last_name=(ldap_profile.get("last_name") or "").strip(),
                is_active=True,
            )
            user.set_unusable_password()
            user.save()
            return user

        updates = []
        if username and user.username != username[:150]:
            user.username = username[:150]
            updates.append("username")
        if resolved_email and user.email != resolved_email:
            user.email = resolved_email
            updates.append("email")

        first_name = (ldap_profile.get("first_name") or "").strip()
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            updates.append("first_name")

        last_name = (ldap_profile.get("last_name") or "").strip()
        if last_name and user.last_name != last_name:
            user.last_name = last_name
            updates.append("last_name")

        if updates:
            user.save(update_fields=updates)

        return user

    def _fallback_email(self, username: str):
        base = re.sub(r"[^a-zA-Z0-9._-]+", "", username or "").strip("._-").lower()
        if not base:
            base = "ldap.user"
        return f"{base}@ldap.local"
