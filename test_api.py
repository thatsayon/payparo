import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.excrow.models import Escrow
from app.excrow.serializers import OrderHistoryDetailSerializer

escrow = Escrow.objects.last()
if escrow:
    # Dummy request context
    class DummyRequest:
        user = escrow.created_by
        def build_absolute_uri(self, url):
            return url
    
    serializer = OrderHistoryDetailSerializer(escrow, context={"request": DummyRequest()})
    try:
        data = serializer.data
        print(json.dumps(data, indent=2, default=str))
    except Exception as e:
        import traceback
        traceback.print_exc()
else:
    print("No escrow found")
