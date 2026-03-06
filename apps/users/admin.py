"""
Users Admin
"""
from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from apps.users.models import User, UserNotification, UserSession, UserPreference


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['email', 'username', 'get_full_name', 'role', 'group_names', 'is_active', 'is_staff']
    list_filter = ['role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'facilities']
    search_fields = ['email', 'username', 'first_name', 'last_name']
    
    fieldsets = (
        (None, {'fields': ('email', 'username', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'phone')}),
        ('Role & Scope', {'fields': ('role', 'facilities', 'primary_facility')}),
        ('Professional Info', {'fields': ('professional_id', 'specialty', 'department')}),
        ('Permissions', {'fields': ('groups', 'user_permissions')}),
        ('System Flags', {'fields': ('is_active', 'is_staff', 'is_superuser', 'email_verified')}),
        ('Preferences', {'fields': ('preferences',), 'classes': ('collapse',)}),
        ('Important dates', {'fields': ('last_login', 'created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'username', 'password1', 'password2', 'role'),
        }),
    )
    
    readonly_fields = ['last_login', 'created_at', 'updated_at']
    ordering = ['email']
    filter_horizontal = ('facilities', 'groups', 'user_permissions')

    def group_names(self, obj):
        return ', '.join(obj.groups.values_list('name', flat=True)) or '—'

    group_names.short_description = 'Groups'


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ['user', 'ip_address', 'login_at', 'logout_at', 'is_active']
    list_filter = ['is_active', 'login_at']
    search_fields = ['user__email', 'ip_address', 'session_key']
    readonly_fields = ['session_key', 'login_at', 'logout_at', 'created_at', 'updated_at']


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ['user', 'preference_type', 'preference_key']
    list_filter = ['preference_type']
    search_fields = ['user__email', 'preference_key']


@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_display = ['recipient', 'sender', 'title', 'category', 'email_sent', 'read_at', 'created_at']
    list_filter = ['category', 'email_sent', 'read_at', 'created_at']
    search_fields = ['recipient__email', 'sender__email', 'title', 'message']
    readonly_fields = ['emailed_at', 'read_at', 'created_at', 'updated_at']


try:
    admin.site.unregister(Group)
except NotRegistered:
    pass


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin):
    list_display = ('name', 'member_count', 'permission_count')
    search_fields = ('name',)
    filter_horizontal = ('permissions',)

    def member_count(self, obj):
        return obj.user_set.count()

    member_count.short_description = 'Users'

    def permission_count(self, obj):
        return obj.permissions.count()

    permission_count.short_description = 'Permissions'


def superuser_only_admin(request):
    return request.user.is_active and request.user.is_superuser


admin.site.has_permission = superuser_only_admin
