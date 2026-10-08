"""
Regression test for ObjectChange.apply()'s raw-save bug on CREATE changes.

deserialize_object() + DeserializedObject.save() always performs a raw save
(Django's save_base(..., raw=True)), which skips every field's pre_save()
hook. That hook is what computes 'last_updated' (auto_now) on every save,
so replaying a branch's CREATE change into main left 'last_updated' NULL
forever, since NetBox's own changelog snapshot never carries it either
('last_updated' is excluded from serialize_object() when
CHANGELOG_SKIP_EMPTY_CHANGES is enabled, NetBox's default).
"""
import uuid

from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import RequestFactory, TransactionTestCase
from django.urls import reverse
from netbox.context import current_request
from netbox.context_managers import event_tracking

from netbox_branching.contextvars import active_branch as active_branch_var
from netbox_branching.tests.utils import provision_branch
from netbox_branching.utilities import activate_branch

User = get_user_model()


class ApplyBackfillTestCase(TransactionTestCase):
    """
    Verify that fields computed via a custom Field.pre_save() (NetBox's
    NaturalOrderingField '_name', Django's auto_now 'last_updated') come out
    correctly populated in main after a branch's CREATE change is merged.
    """
    serialized_rollback = True

    def setUp(self):
        self.user = User.objects.create_user(username='testuser')
        self.manufacturer = Manufacturer.objects.create(name='Manufacturer 1', slug='manufacturer-1')
        self.device_type = DeviceType.objects.create(
            manufacturer=self.manufacturer,
            model='Device Type 1',
            slug='device-type-1',
        )
        self.device_role = DeviceRole.objects.create(name='Device Role 1', slug='device-role-1')
        self.site = Site.objects.create(name='Site 1', slug='site-1')

    def tearDown(self):
        active_branch_var.set(None)
        current_request.set(None)
        for alias in list(vars(connections._connections)):
            if alias.startswith('schema_'):
                connections[alias].close()

    def _make_request(self):
        request = RequestFactory().get(reverse('home'))
        request.id = uuid.uuid4()
        request.user = self.user
        return request

    def test_merged_interface_has_last_updated_and_name_populated(self):
        branch = provision_branch(user=self.user, name='Test Branch')

        with activate_branch(branch), event_tracking(self._make_request()):
            device = Device.objects.create(
                name='Branch Device',
                device_type=self.device_type,
                role=self.device_role,
                site=self.site,
            )
            interface = Interface.objects.create(device=device, name='1:1', type='1000base-t')

        branch.merge(user=self.user)

        interface.refresh_from_db()
        self.assertIsNotNone(interface.last_updated)
        self.assertIsNotNone(interface.created)
        self.assertTrue(interface._name)
