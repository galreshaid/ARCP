from django.contrib.auth import authenticate
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.core.constants import UserRole
from apps.users.forms import SystemAdminUserForm
from apps.users.models import User, UserNotification, UserPreference


class UsersAuthTests(TestCase):
    def test_login_page_renders(self):
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sign In to AAML RadCore Platform')

    def test_default_groups_exist(self):
        self.assertTrue(Group.objects.filter(name='Radiologist').exists())
        self.assertTrue(Group.objects.filter(name='Technologist').exists())
        self.assertTrue(Group.objects.filter(name='Admin').exists())

    def test_group_permission_grants_access(self):
        user = User.objects.create_user(
            email='tech@example.com',
            password='password123',
            username='tech',
            first_name='Tech',
            last_name='User',
        )
        group = Group.objects.create(name='Custom Operators')
        permission = Permission.objects.get(
            content_type__app_label='users',
            codename='protocol_view',
        )

        group.permissions.add(permission)
        user.groups.add(group)

        self.assertTrue(user.has_permission('protocol.view'))

    def test_admin_group_marks_user_as_staff(self):
        user = User.objects.create_user(
            email='admin-group@example.com',
            password='password123',
            username='admingroup',
            first_name='Admin',
            last_name='Group',
        )
        admin_group = Group.objects.get(name='Admin')

        user.groups.add(admin_group)
        user.refresh_from_db()

        self.assertTrue(user.is_staff)

    def test_local_auth_backend_accepts_email(self):
        user = User.objects.create_user(
            email='email-login@example.com',
            password='password123',
            username='emaillogin',
            first_name='Email',
            last_name='Login',
        )

        authenticated = authenticate(username='email-login@example.com', password='password123')

        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, user.pk)

    def test_local_auth_backend_accepts_username(self):
        user = User.objects.create_user(
            email='username-login@example.com',
            password='password123',
            username='usernamelogin',
            first_name='Username',
            last_name='Login',
        )

        authenticated = authenticate(username='usernamelogin', password='password123')

        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, user.pk)

    def test_login_view_accepts_email(self):
        user = User.objects.create_user(
            email='login-view-email@example.com',
            password='password123',
            username='loginviewemail',
            first_name='Login',
            last_name='Email',
        )

        response = self.client.post(
            reverse('login'),
            {
                'username': user.email,
                'password': 'password123',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('home'))

    def test_login_view_accepts_username(self):
        user = User.objects.create_user(
            email='login-view-username@example.com',
            password='password123',
            username='loginviewusername',
            first_name='Login',
            last_name='Username',
        )

        response = self.client.post(
            reverse('login'),
            {
                'username': user.username,
                'password': 'password123',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('home'))

    def test_inbox_page_renders_notifications(self):
        user = User.objects.create_user(
            email='inbox@example.com',
            password='password123',
            username='inboxuser',
            first_name='Inbox',
            last_name='User',
        )
        UserNotification.objects.create(
            recipient=user,
            title='Workflow update',
            message='Technologist confirmed the assigned protocol.',
            category='PROTOCOL_CONFIRMATION',
            target_url='/protocoling/',
        )

        self.client.force_login(user)
        response = self.client.get(reverse('user-inbox'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Message Center')
        self.assertContains(response, 'Workflow update')
        self.assertContains(response, 'Send + Email')

    def test_open_notification_marks_it_read(self):
        user = User.objects.create_user(
            email='read@example.com',
            password='password123',
            username='readuser',
            first_name='Read',
            last_name='User',
        )
        notification = UserNotification.objects.create(
            recipient=user,
            title='Read test',
            message='Open this item to mark it read.',
            target_url='/protocoling/',
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse('user-notification-open', args=[notification.id]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/protocoling/')
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

    def test_delete_notification_endpoint_removes_inbox_item(self):
        user = User.objects.create_user(
            email='delete@example.com',
            password='password123',
            username='deleteuser',
            first_name='Delete',
            last_name='User',
        )
        notification = UserNotification.objects.create(
            recipient=user,
            title='Delete me',
            message='Remove this item from the inbox.',
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse('user-notification-delete', args=[notification.id]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('user-inbox'))
        self.assertFalse(UserNotification.objects.filter(id=notification.id).exists())

    def test_send_notification_endpoint_creates_direct_message(self):
        sender = User.objects.create_user(
            email='sender@example.com',
            password='password123',
            username='senderuser',
            first_name='Sender',
            last_name='User',
        )
        recipient = User.objects.create_user(
            email='recipient@example.com',
            password='password123',
            username='recipientuser',
            first_name='Recipient',
            last_name='User',
        )

        self.client.force_login(sender)
        response = self.client.post(
            reverse('user-notification-send'),
            {
                'message_recipient_id': str(recipient.id),
                'message_title': 'Quick update',
                'message_body': 'Please check the latest workflow change.',
                'target_url': '/protocoling/',
            },
            HTTP_REFERER='/protocoling/',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/protocoling/')
        self.assertEqual(UserNotification.objects.count(), 1)
        notification = UserNotification.objects.get()
        self.assertEqual(notification.recipient, recipient)
        self.assertEqual(notification.sender, sender)
        self.assertEqual(notification.category, 'DIRECT_MESSAGE')
        self.assertEqual(notification.title, 'Quick update')
        self.assertEqual(notification.target_url, '/protocoling/')
        self.assertTrue(notification.email_sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [recipient.email])


class SystemAdminUserFormTests(TestCase):
    def _base_data(self, **overrides):
        data = {
            "email": "form-user@example.com",
            "username": "formuser",
            "first_name": "Form",
            "last_name": "User",
            "phone": "",
            "role": UserRole.VIEWER,
            "primary_facility": "",
            "professional_id": "",
            "specialty": "",
            "department": "",
            "is_active": "on",
            "is_staff": "",
            "is_superuser": "",
            "email_verified": "",
            "preferences": "{}",
            "reset_password": "",
            "password": "",
            "password_confirm": "",
        }
        data.update(overrides)
        return data

    def test_create_requires_password_and_confirmation(self):
        form = SystemAdminUserForm(data=self._base_data())

        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)
        self.assertIn("password_confirm", form.errors)

    def test_create_requires_professional_id_and_nid(self):
        form = SystemAdminUserForm(
            data=self._base_data(
                password="NewSecurePass123",
                password_confirm="NewSecurePass123",
                role="",
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("role", form.errors)
        self.assertIn("professional_id", form.errors)
        self.assertIn("nid", form.errors)

    def test_create_with_required_fields_is_valid(self):
        form = SystemAdminUserForm(
            data=self._base_data(
                role=UserRole.VIEWER,
                professional_id="PRO-1001",
                nid="NID-1001",
                password="NewSecurePass123",
                password_confirm="NewSecurePass123",
            )
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        saved_user = form.save()
        self.assertEqual(saved_user.professional_id, "PRO-1001")
        self.assertEqual(saved_user.nid, "NID-1001")

    def test_update_without_reset_keeps_password(self):
        user = User.objects.create_user(
            email="existing-user@example.com",
            password="CurrentPass123",
            username="existinguser",
            first_name="Existing",
            last_name="User",
            role=UserRole.VIEWER,
        )
        original_hash = user.password

        form = SystemAdminUserForm(
            instance=user,
            data=self._base_data(
                email=user.email,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            ),
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        saved_user = form.save()
        self.assertEqual(saved_user.password, original_hash)
        self.assertTrue(saved_user.check_password("CurrentPass123"))

    def test_update_with_reset_changes_password(self):
        user = User.objects.create_user(
            email="reset-user@example.com",
            password="CurrentPass123",
            username="resetuser",
            first_name="Reset",
            last_name="User",
            role=UserRole.VIEWER,
        )

        form = SystemAdminUserForm(
            instance=user,
            data=self._base_data(
                email=user.email,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                reset_password="on",
                password="NewSecurePass123",
                password_confirm="NewSecurePass123",
            ),
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        saved_user = form.save()
        self.assertTrue(saved_user.check_password("NewSecurePass123"))

    def test_update_with_mismatched_reset_password_rejected(self):
        user = User.objects.create_user(
            email="mismatch-user@example.com",
            password="CurrentPass123",
            username="mismatchuser",
            first_name="Mismatch",
            last_name="User",
            role=UserRole.VIEWER,
        )

        form = SystemAdminUserForm(
            instance=user,
            data=self._base_data(
                email=user.email,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                reset_password="on",
                password="NewSecurePass123",
                password_confirm="WrongPass123",
            ),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("password_confirm", form.errors)

    def test_update_can_upsert_user_preference(self):
        user = User.objects.create_user(
            email="pref-user@example.com",
            password="CurrentPass123",
            username="prefuser",
            first_name="Pref",
            last_name="User",
            role=UserRole.VIEWER,
        )

        form = SystemAdminUserForm(
            instance=user,
            data=self._base_data(
                email=user.email,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                preference_type="display",
                preference_key="default_modality",
                preference_value='{"value":"CT"}',
            ),
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()

        preference = UserPreference.objects.get(
            user=user,
            preference_type="display",
            preference_key="default_modality",
        )
        self.assertEqual(preference.preference_value, {"value": "CT"})

    def test_update_can_set_user_level_app_permissions(self):
        user = User.objects.create_user(
            email="perm-user@example.com",
            password="CurrentPass123",
            username="permuser",
            first_name="Perm",
            last_name="User",
            role=UserRole.VIEWER,
        )

        form = SystemAdminUserForm(
            instance=user,
            data=self._base_data(
                email=user.email,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                app_permissions=["qc.view", "protocol.view"],
            ),
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        user.refresh_from_db()
        self.assertTrue(user.has_perm("users.qc_view"))
        self.assertTrue(user.has_perm("users.protocol_view"))
