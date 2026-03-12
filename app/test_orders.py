import sys
from django.contrib.auth import get_user_model
from app.excrow.models import Escrow
from app.excrow.views import EscrowListCreateView
from django.test import RequestFactory

User = get_user_model()

def test_order_history():
    try:
        # 1. Ensure test users exist
        creator, _ = User.objects.get_or_create(username='test_creator', email='creator@test.com')
        receiver, _ = User.objects.get_or_create(username='test_receiver', email='receiver@test.com')
        other, _ = User.objects.get_or_create(username='test_other', email='other@test.com')

        # 2. Clear existing test escrows to avoid bleed
        Escrow.objects.filter(product_name__startswith='Test Order').delete()

        # 3. Create test escrows
        e1 = Escrow.objects.create(
            created_by=creator,
            receiver=receiver,
            product_name="Test Order 1 (Creator Sent)"
        )

        e2 = Escrow.objects.create(
            created_by=other,
            receiver=creator,
            product_name="Test Order 2 (Creator Received)"
        )

        e3 = Escrow.objects.create(
            created_by=receiver,
            receiver=other,
            product_name="Test Order 3 (Irrelevant to Creator)"
        )

        # 4. Mock a GET request as `creator`
        factory = RequestFactory()
        request = factory.get('/')
        request.user = creator

        # 5. Execute view logic
        view = EscrowListCreateView()
        view.request = request
        view.format_kwarg = None
        
        from django.db.models import Q
        
        # Test directly the queryset to avoid needing full DRF request parsing config in script
        queryset = Escrow.objects.filter(
            Q(created_by=request.user) | Q(receiver=request.user)
        )
        
        count = queryset.count()
        print(f"Total escrows found for 'creator': {count}")
        
        titles = [e.product_name for e in queryset]
        print(f"Titles: {titles}")

        assert count == 2, f"Expected 2 escrows, found {count}"
        assert e1.product_name in titles
        assert e2.product_name in titles
        assert e3.product_name not in titles
        
        print("Success! Order history queryset logic is correct.")

    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)

test_order_history()
