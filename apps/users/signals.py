from django.apps import apps
from django.db.models.signals import m2m_changed, post_migrate
from django.dispatch import receiver

from apps.users.models import (
    DEFAULT_GROUP_PERMISSIONS,
    DOMAIN_PERMISSION_TO_DJANGO_PERMISSION,
    User,
)


@receiver(post_migrate)
def ensure_default_groups(sender, app_config, **kwargs):
    if app_config.name != 'apps.users':
        return

    group_model = apps.get_model('auth', 'Group')
    permission_model = apps.get_model('auth', 'Permission')

    for group_name, domain_permissions in DEFAULT_GROUP_PERMISSIONS.items():
        group, _ = group_model.objects.get_or_create(name=group_name)
        permission_codenames = [
            DOMAIN_PERMISSION_TO_DJANGO_PERMISSION[permission].split('.', 1)[1]
            for permission in domain_permissions
        ]
        permissions = permission_model.objects.filter(
            content_type__app_label='users',
            codename__in=permission_codenames,
        )
        if permissions:
            group.permissions.add(*permissions)


@receiver(m2m_changed, sender=User.groups.through)
def grant_staff_access_for_admin_group(sender, instance, action, pk_set, **kwargs):
    if action != 'post_add' or not pk_set or instance.is_staff:
        return

    group_model = apps.get_model('auth', 'Group')
    if group_model.objects.filter(pk__in=pk_set, name='Admin').exists():
        instance.is_staff = True
        instance.save(update_fields=['is_staff'])
