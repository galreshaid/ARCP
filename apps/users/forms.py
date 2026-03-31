import json
import string

from django import forms
from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.auth.forms import AuthenticationForm
from django.core.mail import send_mail
from django.utils.crypto import get_random_string

from apps.core.models import Modality
from apps.core.services.subspeciality import SUBSPECIALITY_POOL, normalize_subspeciality
from apps.qc.services.access import parse_modality_codes
from apps.users.models import DOMAIN_PERMISSION_TO_DJANGO_PERMISSION, User, UserPreference


class EmailAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Username or Email'
        self.fields['username'].widget.attrs.update({
            'placeholder': 'Username or email',
            'autocomplete': 'username',
        })
        self.fields['password'].widget.attrs.update({
            'placeholder': 'Password',
            'autocomplete': 'current-password',
        })


def _generate_temporary_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return get_random_string(length=length, allowed_chars=alphabet)


def _send_temporary_password_email(user: User, temporary_password: str):
    recipient_email = str(getattr(user, "email", "") or "").strip()
    if not recipient_email:
        return

    from_email = (
        getattr(settings, "DEFAULT_FROM_EMAIL", "")
        or getattr(settings, "SERVER_EMAIL", "")
        or "no-reply@localhost"
    )
    subject = "AAML account password reset"
    message = "\n".join(
        [
            "Your account password was reset by system administration.",
            "",
            f"Temporary password: {temporary_password}",
            "",
            "You must change this password at your first sign in.",
        ]
    )
    send_mail(
        subject=subject,
        message=message,
        from_email=from_email,
        recipient_list=[recipient_email],
        fail_silently=True,
    )


class SystemAdminUserForm(forms.ModelForm):
    reset_password = forms.BooleanField(
        required=False,
        initial=False,
        label='Reset password',
        help_text='Enable this option to set a new password for this user.',
    )
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label='New password',
        help_text='Use at least 8 characters.',
    )
    password_confirm = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label='Confirm new password',
        help_text='Re-enter the new password to avoid typing errors.',
    )
    preference_type = forms.ChoiceField(
        required=False,
        choices=[('', 'Select preference type')] + list(UserPreference.PREFERENCE_TYPES),
        label='Add preference type',
        help_text='Optional: add or update one user preference entry on save.',
    )
    preference_key = forms.CharField(
        required=False,
        label='Preference key',
        max_length=100,
        help_text='Example: default_modality, queue_sort, dashboard_layout.',
    )
    preference_value = forms.CharField(
        required=False,
        label='Preference value (JSON)',
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Enter valid JSON, for example: {"default_modality":"CT"}',
    )
    app_permissions = forms.MultipleChoiceField(
        required=False,
        label='User-level app permissions',
        widget=forms.CheckboxSelectMultiple(),
        help_text='Direct permissions for this user. These apply in addition to group permissions.',
    )
    qc_modalities = forms.MultipleChoiceField(
        required=False,
        label='QC Modality Scope',
        widget=forms.CheckboxSelectMultiple(),
        help_text='Select modality codes this supervisor can manage in QC worklist (checkboxes).',
    )
    preference_notify_qc = forms.BooleanField(
        required=False,
        initial=True,
        label="Notify for QC updates",
    )
    preference_notify_protocol = forms.BooleanField(
        required=False,
        initial=True,
        label="Notify for Protocol updates",
    )
    preference_notify_contrast = forms.BooleanField(
        required=False,
        initial=True,
        label="Notify for Contrast updates",
    )
    preference_notify_email = forms.BooleanField(
        required=False,
        initial=True,
        label="Send notifications by email",
    )

    class Meta:
        model = User
        fields = (
            'email',
            'username',
            'first_name',
            'last_name',
            'phone',
            'role',
            'facilities',
            'primary_facility',
            'professional_id',
            'nid',
            'specialty',
            'department',
            'groups',
            'is_active',
            'is_staff',
            'is_superuser',
            'email_verified',
        )

    def _is_create_mode(self) -> bool:
        return bool(getattr(self.instance._state, 'adding', False))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_create_mode = self._is_create_mode()
        is_existing_user = not self.is_create_mode
        self.fields['facilities'].required = True
        self.fields['groups'].required = False
        self.fields['professional_id'].label = 'Employee ID'
        self.fields['specialty'].label = 'Default Subspecialty'
        self.fields['specialty'].help_text = (
            'Default subspecialty filter for Protocol Worklist. Users can clear it from the worklist filters.'
        )
        self.fields['specialty'].widget = forms.Select(
            choices=[('', '---------')] + [(value, value) for value in SUBSPECIALITY_POOL]
        )
        self.fields['facilities'].help_text = 'Select one or more facilities. This field is required.'
        self.fields['groups'].queryset = self.fields['groups'].queryset.order_by('name')
        self.fields['groups'].widget = forms.CheckboxSelectMultiple()
        self.fields['groups'].help_text = 'Assign role groups. Group permissions are applied automatically.'
        modality_choices = [
            (
                str(modality.code or "").strip().upper(),
                f"{str(modality.code or '').strip().upper()} - {str(modality.name or '').strip()}",
            )
            for modality in Modality.objects.filter(is_active=True).order_by('code')
            if str(modality.code or "").strip()
        ]
        self.fields["qc_modalities"].choices = modality_choices

        permission_labels = {
            "qc.view": "QC: View",
            "qc.create": "QC: Create",
            "qc.edit": "QC: Edit",
            "qc.approve": "QC: Approve",
            "qc.evidence_capture": "QC: Evidence Capture",
            "qc.evidence_view": "QC: Evidence View",
            "qc.notify_modality_supervisor": "QC: Notify Modality Supervisor",
            "qc.notify_modality_qc_supervisor": "QC: Notify Modality QC Supervisor",
            "qc.notify_officer": "QC: Notify QC Officer",
            "protocol.view": "Protocol: View",
            "protocol.assign": "Protocol: Assign",
            "protocol.edit": "Protocol: Edit",
            "contrast.view": "Contrast: View",
            "contrast.create": "Contrast: Create",
            "contrast.edit": "Contrast: Edit",
            "contrast.approve": "Contrast: Approve",
            "report.view": "Reports: View",
            "report.export": "Reports: Export",
            "admin.access": "Administration: Access",
            "audit.view": "Audit: View",
            "material_catalog.add": "Material Catalog: Add",
            "material_catalog.edit": "Material Catalog: Edit",
        }
        self.fields["app_permissions"].choices = [
            (domain_permission, permission_labels.get(domain_permission, domain_permission))
            for domain_permission in DOMAIN_PERMISSION_TO_DJANGO_PERMISSION.keys()
        ]

        if is_existing_user:
            assigned_codenames = set(
                self.instance.user_permissions.filter(content_type__app_label='users')
                .values_list('codename', flat=True)
            )
            initial_permissions = []
            for domain_permission, django_permission in DOMAIN_PERMISSION_TO_DJANGO_PERMISSION.items():
                codename = django_permission.split('.', 1)[-1]
                if codename in assigned_codenames:
                    initial_permissions.append(domain_permission)
            self.initial['app_permissions'] = initial_permissions

            preference_payload = dict(getattr(self.instance, "preferences", {}) or {})
            initial_modality_codes = set(
                parse_modality_codes(preference_payload.get("qc_modalities"))
            )
            if not initial_modality_codes:
                preference_rows = UserPreference.objects.filter(
                    user=self.instance,
                    preference_type="qc_worklist_filter",
                    preference_key__in=("modalities", "modality_codes", "qc_modalities"),
                ).values_list("preference_value", flat=True)
                for preference_value in preference_rows:
                    initial_modality_codes.update(parse_modality_codes(preference_value))

            available_codes = {choice[0] for choice in modality_choices}
            self.initial["qc_modalities"] = sorted(
                code for code in initial_modality_codes
                if code in available_codes
            )
            notification_preferences = preference_payload.get("notification")
            if isinstance(notification_preferences, dict):
                self.initial["preference_notify_qc"] = bool(notification_preferences.get("qc", True))
                self.initial["preference_notify_protocol"] = bool(notification_preferences.get("protocol", True))
                self.initial["preference_notify_contrast"] = bool(notification_preferences.get("contrast", True))
                self.initial["preference_notify_email"] = bool(notification_preferences.get("email", True))

        if self.is_create_mode:
            self.fields['reset_password'].initial = False
            self.fields['password'].required = True
            self.fields['password_confirm'].required = True
            self.fields['professional_id'].required = True
            self.fields['nid'].required = True
            self.fields['role'].required = True
            role_choices = list(self.fields['role'].choices)
            self.fields['role'].choices = [('', 'Select role')] + role_choices
            self.fields['role'].initial = ''
            self.fields['password'].help_text = 'Required when creating a new user.'
            self.fields['password_confirm'].help_text = 'Required when creating a new user.'
            self.fields['reset_password'].help_text = 'Optional for new user. If enabled, a temporary password is generated and emailed.'
            self.fields['professional_id'].help_text = 'Employee ID is required when creating a new user.'
            self.fields['nid'].help_text = 'Required when creating a new user.'
            self.fields['role'].help_text = 'Choose the operational role for this user.'
        else:
            self.fields['reset_password'].help_text = (
                'Generate and email a temporary password automatically. '
                'User will be forced to change password at first login.'
            )
            self.fields['password'].help_text = 'Not required for reset. Leave blank unless creating a new account.'
            self.fields['password_confirm'].help_text = 'Not required for reset.'

    def clean(self):
        cleaned_data = super().clean()
        password = str(cleaned_data.get('password') or '')
        password_confirm = str(cleaned_data.get('password_confirm') or '')
        reset_password = bool(cleaned_data.get('reset_password'))
        is_existing_user = not getattr(self, 'is_create_mode', self._is_create_mode())
        must_set_password = not is_existing_user
        preference_type = str(cleaned_data.get('preference_type') or '').strip()
        preference_key = str(cleaned_data.get('preference_key') or '').strip()
        raw_preference_value = str(cleaned_data.get('preference_value') or '').strip()

        if must_set_password and not password:
            self.add_error('password', 'Enter a new password.')
        if must_set_password and not password_confirm:
            self.add_error('password_confirm', 'Confirm the new password.')
        if password and len(password) < 8 and not (reset_password and is_existing_user):
            self.add_error('password', 'Password must be at least 8 characters.')
        if password and password_confirm and password != password_confirm and not (reset_password and is_existing_user):
            self.add_error('password_confirm', 'Passwords do not match.')

        if password_confirm and not password and not must_set_password:
            self.add_error('password', 'Enter a new password before confirming.')
        if reset_password and is_existing_user:
            cleaned_data['password'] = ''
            cleaned_data['password_confirm'] = ''

        primary_facility = cleaned_data.get('primary_facility')
        facilities = cleaned_data.get('facilities')
        facilities_field_queryset = self.fields['facilities'].queryset

        selected_facility_ids = set()
        if facilities is not None:
            selected_facility_ids.update(
                str(value).strip()
                for value in facilities.values_list('id', flat=True)
                if str(value).strip()
            )

        if primary_facility:
            normalized_primary_id = str(primary_facility.id).strip()
            if normalized_primary_id:
                selected_facility_ids.add(normalized_primary_id)

        if selected_facility_ids:
            cleaned_data['facilities'] = facilities_field_queryset.filter(
                id__in=selected_facility_ids
            )
            facilities = cleaned_data['facilities']

        if not facilities:
            self.add_error('facilities', 'Select at least one facility.')

        specialty_value = normalize_subspeciality(cleaned_data.get('specialty'))
        cleaned_data['specialty'] = specialty_value
        selected_modalities = cleaned_data.get("qc_modalities") or []
        normalized_modalities = []
        seen_modalities = set()
        for modality_code in selected_modalities:
            normalized_code = str(modality_code or "").strip().upper()
            if not normalized_code or normalized_code in seen_modalities:
                continue
            seen_modalities.add(normalized_code)
            normalized_modalities.append(normalized_code)
        cleaned_data["qc_modalities"] = normalized_modalities

        if preference_type or preference_key or raw_preference_value:
            if not preference_type:
                self.add_error('preference_type', 'Select a preference type.')
            if not preference_key:
                self.add_error('preference_key', 'Enter a preference key.')
            if not raw_preference_value:
                self.add_error('preference_value', 'Enter a JSON value.')
            if raw_preference_value:
                try:
                    cleaned_data['_parsed_preference_value'] = json.loads(raw_preference_value)
                except json.JSONDecodeError:
                    self.add_error('preference_value', 'Preference value must be valid JSON.')

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = str(self.cleaned_data.get('password') or '')
        reset_password = bool(self.cleaned_data.get('reset_password'))
        generated_temporary_password = ""
        is_create_mode = getattr(self, 'is_create_mode', self._is_create_mode())
        is_existing_user = not is_create_mode

        if reset_password and is_existing_user:
            generated_temporary_password = _generate_temporary_password()
            user.set_password(generated_temporary_password)
            user.must_change_password = True
        elif password:
            user.set_password(password)
            if is_create_mode:
                user.must_change_password = False

        if commit:
            user.save()
            self.save_m2m()

            selected_app_permissions = self.cleaned_data.get('app_permissions') or []
            selected_codenames = [
                DOMAIN_PERMISSION_TO_DJANGO_PERMISSION[domain_permission].split('.', 1)[-1]
                for domain_permission in selected_app_permissions
                if domain_permission in DOMAIN_PERMISSION_TO_DJANGO_PERMISSION
            ]
            selected_permissions = Permission.objects.filter(
                content_type__app_label='users',
                codename__in=selected_codenames,
            )
            user.user_permissions.set(selected_permissions)
            selected_qc_modalities = self.cleaned_data.get("qc_modalities") or []
            updated_preferences = dict(getattr(user, "preferences", {}) or {})
            if selected_qc_modalities:
                updated_preferences["qc_modalities"] = selected_qc_modalities
            else:
                updated_preferences.pop("qc_modalities", None)
            updated_preferences["notification"] = {
                "qc": bool(self.cleaned_data.get("preference_notify_qc")),
                "protocol": bool(self.cleaned_data.get("preference_notify_protocol")),
                "contrast": bool(self.cleaned_data.get("preference_notify_contrast")),
                "email": bool(self.cleaned_data.get("preference_notify_email")),
            }
            user.preferences = updated_preferences
            user.save(update_fields=["preferences"])

            preference_type = str(self.cleaned_data.get('preference_type') or '').strip()
            preference_key = str(self.cleaned_data.get('preference_key') or '').strip()
            parsed_preference_value = self.cleaned_data.get('_parsed_preference_value')
            if preference_type and preference_key and parsed_preference_value is not None:
                UserPreference.objects.update_or_create(
                    user=user,
                    preference_type=preference_type,
                    preference_key=preference_key,
                    defaults={'preference_value': parsed_preference_value},
                )
            if generated_temporary_password:
                _send_temporary_password_email(user, generated_temporary_password)

        return user
