import json

from django import forms
from django.contrib.auth.models import Permission
from django.contrib.auth.forms import AuthenticationForm

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
            'preferences',
            'password',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['facilities'].required = False
        self.fields['groups'].required = False
        self.fields['professional_id'].label = 'Employee ID'
        self.fields['groups'].queryset = self.fields['groups'].queryset.order_by('name')
        self.fields['groups'].widget = forms.CheckboxSelectMultiple()
        self.fields['groups'].help_text = 'Assign role groups. Group permissions are applied automatically.'

        permission_labels = {
            "qc.view": "QC: View",
            "qc.create": "QC: Create",
            "qc.edit": "QC: Edit",
            "qc.approve": "QC: Approve",
            "qc.evidence_capture": "QC: Evidence Capture",
            "qc.evidence_view": "QC: Evidence View",
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

        if self.instance.pk:
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

        if not self.instance.pk:
            self.fields['reset_password'].initial = True
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
            self.fields['reset_password'].help_text = 'New account requires an initial password.'
            self.fields['professional_id'].help_text = 'Employee ID is required when creating a new user.'
            self.fields['nid'].help_text = 'Required when creating a new user.'
            self.fields['role'].help_text = 'Choose the operational role for this user.'

    def clean(self):
        cleaned_data = super().clean()
        password = str(cleaned_data.get('password') or '')
        password_confirm = str(cleaned_data.get('password_confirm') or '')
        reset_password = bool(cleaned_data.get('reset_password'))
        must_set_password = (not self.instance.pk) or reset_password
        preference_type = str(cleaned_data.get('preference_type') or '').strip()
        preference_key = str(cleaned_data.get('preference_key') or '').strip()
        raw_preference_value = str(cleaned_data.get('preference_value') or '').strip()

        if must_set_password and not password:
            self.add_error('password', 'Enter a new password.')
        if must_set_password and not password_confirm:
            self.add_error('password_confirm', 'Confirm the new password.')
        if password and len(password) < 8:
            self.add_error('password', 'Password must be at least 8 characters.')
        if password and password_confirm and password != password_confirm:
            self.add_error('password_confirm', 'Passwords do not match.')

        if password_confirm and not password and not must_set_password:
            self.add_error('password', 'Enter a new password before confirming.')

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
        password = self.cleaned_data.get('password')

        if password:
            user.set_password(password)

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

        return user
