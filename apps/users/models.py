"""
User Models
Custom user model with role-based access
"""
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from apps.core.models import BaseModel, TimeStampedModel
from apps.core.constants import (
    Permission as AppPermission,
    UserRole,
)


DOMAIN_PERMISSION_TO_DJANGO_PERMISSION = {
    AppPermission.QC_VIEW: 'users.qc_view',
    AppPermission.QC_CREATE: 'users.qc_create',
    AppPermission.QC_EDIT: 'users.qc_edit',
    AppPermission.QC_APPROVE: 'users.qc_approve',
    AppPermission.QC_EVIDENCE_CAPTURE: 'users.qc_evidence_capture',
    AppPermission.QC_EVIDENCE_VIEW: 'users.qc_evidence_view',
    AppPermission.PROTOCOL_VIEW: 'users.protocol_view',
    AppPermission.PROTOCOL_ASSIGN: 'users.protocol_assign',
    AppPermission.PROTOCOL_EDIT: 'users.protocol_edit',
    AppPermission.CONTRAST_VIEW: 'users.contrast_view',
    AppPermission.CONTRAST_CREATE: 'users.contrast_create',
    AppPermission.CONTRAST_EDIT: 'users.contrast_edit',
    AppPermission.CONTRAST_APPROVE: 'users.contrast_approve',
    AppPermission.REPORT_VIEW: 'users.report_view',
    AppPermission.REPORT_EXPORT: 'users.report_export',
    AppPermission.ADMIN_ACCESS: 'users.admin_access',
    AppPermission.AUDIT_VIEW: 'users.audit_view',
    AppPermission.MATERIAL_CATALOG_ADD: 'users.material_catalog_add',
    AppPermission.MATERIAL_CATALOG_EDIT: 'users.material_catalog_edit',
}

DEFAULT_GROUP_PERMISSIONS = {
    'Radiologist': [
        AppPermission.QC_VIEW,
        AppPermission.QC_CREATE,
        AppPermission.QC_EDIT,
        AppPermission.QC_APPROVE,
        AppPermission.QC_EVIDENCE_CAPTURE,
        AppPermission.QC_EVIDENCE_VIEW,
        AppPermission.PROTOCOL_VIEW,
        AppPermission.PROTOCOL_ASSIGN,
        AppPermission.CONTRAST_VIEW,
        AppPermission.REPORT_VIEW,
    ],
    'Technologist': [
        AppPermission.CONTRAST_VIEW,
        AppPermission.CONTRAST_CREATE,
        AppPermission.CONTRAST_EDIT,
        AppPermission.QC_VIEW,
        AppPermission.PROTOCOL_VIEW,
    ],
    'Admin': list(DOMAIN_PERMISSION_TO_DJANGO_PERMISSION.keys()),
    'Supervisor': [
        AppPermission.QC_VIEW,
        AppPermission.QC_EDIT,
        AppPermission.QC_APPROVE,
        AppPermission.QC_EVIDENCE_CAPTURE,
        AppPermission.QC_EVIDENCE_VIEW,
        AppPermission.PROTOCOL_VIEW,
        AppPermission.CONTRAST_VIEW,
        AppPermission.CONTRAST_APPROVE,
        AppPermission.REPORT_VIEW,
        AppPermission.REPORT_EXPORT,
        AppPermission.AUDIT_VIEW,
    ],
    'Finance': [
        AppPermission.CONTRAST_VIEW,
        AppPermission.REPORT_VIEW,
        AppPermission.REPORT_EXPORT,
    ],
}


class UserManager(BaseUserManager):
    """
    Custom user manager
    """
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', UserRole.ADMIN)
        
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    """
    Custom User model
    """
    # Authentication
    email = models.EmailField(_('Email'), unique=True, db_index=True)
    username = models.CharField(_('Username'), max_length=150, unique=True)
    
    # Personal Info
    first_name = models.CharField(_('First Name'), max_length=150)
    last_name = models.CharField(_('Last Name'), max_length=150)
    phone = models.CharField(_('Phone'), max_length=50, blank=True)
    
    # Role & Permissions
    role = models.CharField(
        _('Role'),
        max_length=20,
        choices=[
            (UserRole.RADIOLOGIST, 'Radiologist'),
            (UserRole.TECHNOLOGIST, 'Technologist'),
            (UserRole.SUPERVISOR, 'Supervisor'),
            (UserRole.FINANCE, 'Finance'),
            (UserRole.ADMIN, 'Administrator'),
            (UserRole.VIEWER, 'Viewer'),
        ],
        default=UserRole.VIEWER
    )
    
    # Facility Access (Multi-hospital)
    facilities = models.ManyToManyField(
        'core.Facility',
        related_name='users',
        blank=True,
        verbose_name=_('Facilities')
    )
    primary_facility = models.ForeignKey(
        'core.Facility',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='primary_users',
        verbose_name=_('Primary Facility')
    )
    
    # Professional Info
    professional_id = models.CharField(_('Employee ID'), max_length=100, blank=True)
    nid = models.CharField(_('NID'), max_length=30, blank=True, db_index=True)
    specialty = models.CharField(_('Specialty'), max_length=100, blank=True)
    department = models.CharField(_('Department'), max_length=100, blank=True)
    
    # System flags
    is_active = models.BooleanField(_('Active'), default=True)
    is_staff = models.BooleanField(_('Staff Status'), default=False)
    email_verified = models.BooleanField(_('Email Verified'), default=False)
    
    # Preferences
    preferences = models.JSONField(_('User Preferences'), default=dict, blank=True)
    
    # Timestamps
    last_login_ip = models.GenericIPAddressField(_('Last Login IP'), null=True, blank=True)
    
    objects = UserManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'first_name', 'last_name']

    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')
        ordering = ['last_name', 'first_name']
        permissions = (
            ('qc_view', 'Can view QC workflows'),
            ('qc_create', 'Can create QC records'),
            ('qc_edit', 'Can edit QC records'),
            ('qc_approve', 'Can approve QC records'),
            ('qc_evidence_capture', 'Can capture QC evidence'),
            ('qc_evidence_view', 'Can view QC evidence'),
            ('protocol_view', 'Can view protocol workflows'),
            ('protocol_assign', 'Can assign protocols'),
            ('protocol_edit', 'Can edit protocols'),
            ('contrast_view', 'Can view contrast workflows'),
            ('contrast_create', 'Can create contrast records'),
            ('contrast_edit', 'Can edit contrast records'),
            ('contrast_approve', 'Can approve contrast records'),
            ('report_view', 'Can view reports'),
            ('report_export', 'Can export reports'),
            ('admin_access', 'Can access administrative areas'),
            ('audit_view', 'Can view audit records'),
            ('material_catalog_add', 'Can add material catalog records'),
            ('material_catalog_edit', 'Can edit material catalog records'),
        )

    def __str__(self):
        return f"{self.get_full_name()} ({self.email})"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    def has_permission(self, permission: str) -> bool:
        """
        Check if user has a permission via Django's explicit permission model
        (direct user permission or group permission).
        """
        if self.is_superuser:
            return True

        django_permission = DOMAIN_PERMISSION_TO_DJANGO_PERMISSION.get(
            permission,
            permission,
        )

        if '.' in django_permission:
            return self.has_perm(django_permission)

        return False

    def has_facility_access(self, facility) -> bool:
        """
        Check if user has access to specific facility
        """
        if self.is_superuser:
            return True
        
        # Check if facility is in user's facilities
        from apps.core.models import Facility
        if isinstance(facility, str):
            # Facility code provided
            return self.facilities.filter(code=facility).exists()
        elif isinstance(facility, Facility):
            return self.facilities.filter(id=facility.id).exists()
        
        return False

    def can_access_exam(self, exam) -> bool:
        """
        Check if user can access specific exam based on facility
        """
        return self.has_facility_access(exam.facility)


class UserSession(TimeStampedModel):
    """
    Track user sessions for audit
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    session_key = models.CharField(_('Session Key'), max_length=255, unique=True)
    ip_address = models.GenericIPAddressField(_('IP Address'))
    user_agent = models.TextField(_('User Agent'), blank=True)
    login_at = models.DateTimeField(_('Login At'), auto_now_add=True)
    logout_at = models.DateTimeField(_('Logout At'), null=True, blank=True)
    is_active = models.BooleanField(_('Is Active'), default=True)

    class Meta:
        verbose_name = _('User Session')
        verbose_name_plural = _('User Sessions')
        ordering = ['-login_at']

    def __str__(self):
        return f"{self.user.email} - {self.login_at}"


class UserNotification(BaseModel):
    """
    Internal user-to-user notification inbox item.
    """

    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_notifications',
    )
    sender = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_notifications',
    )
    title = models.CharField(_('Title'), max_length=200)
    message = models.TextField(_('Message'))
    category = models.CharField(_('Category'), max_length=40, default='INFO', db_index=True)
    target_url = models.CharField(_('Target URL'), max_length=500, blank=True)
    email_sent = models.BooleanField(_('Email Sent'), default=False, db_index=True)
    emailed_at = models.DateTimeField(_('Emailed At'), null=True, blank=True)
    read_at = models.DateTimeField(_('Read At'), null=True, blank=True, db_index=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)

    class Meta:
        verbose_name = _('User Notification')
        verbose_name_plural = _('User Notifications')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'read_at', '-created_at']),
            models.Index(fields=['recipient', '-created_at']),
            models.Index(fields=['category', '-created_at']),
        ]

    def __str__(self):
        return f"{self.recipient.email} - {self.title}"

    @property
    def is_read(self):
        return self.read_at is not None

    def mark_read(self):
        if self.read_at is not None:
            return

        self.read_at = timezone.now()
        self.save(update_fields=['read_at'])


class UserPreference(models.Model):
    """
    User-specific preferences and settings
    """
    PREFERENCE_TYPES = [
        ('qc_worklist_filter', 'QC Worklist Default Filter'),
        ('protocol_suggestion', 'Protocol Suggestion Settings'),
        ('notification', 'Notification Preferences'),
        ('display', 'Display Settings'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='user_preferences')
    preference_type = models.CharField(_('Type'), max_length=50, choices=PREFERENCE_TYPES)
    preference_key = models.CharField(_('Key'), max_length=100)
    preference_value = models.JSONField(_('Value'))
    
    class Meta:
        unique_together = ['user', 'preference_type', 'preference_key']
        verbose_name = _('User Preference')
        verbose_name_plural = _('User Preferences')

    def __str__(self):
        return f"{self.user.email} - {self.preference_type}: {self.preference_key}"
