import random
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from app.excrow.models import Escrow, EscrowInstallment

User = get_user_model()

class Command(BaseCommand):
    help = 'Seed Escrow model with random data'

    def handle(self, *args, **kwargs):
        try:
            creator = User.objects.get(email="hello@thatsayon.com")
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR('User with email hello@thatsayon.com does not exist. Please create it first.'))
            return

        receivers = list(User.objects.exclude(id=creator.id)[:50])
        if not receivers:
            self.stdout.write(self.style.WARNING('No other users found in the database. Creating a dummy receiver...'))
            dummy_receiver, _ = User.objects.get_or_create(
                email='dummy_receiver@example.com',
                defaults={'username': 'dummy_receiver', 'full_name': 'Dummy Receiver'}
            )
            receivers.append(dummy_receiver)

        product_names = [
            "Web Development Service", "Logo Design", "Used MacBook Pro", 
            "SEO Optimization", "Graphic Design Package", "Custom CRM App",
            "Copywriting Service", "Marketing Strategy", "Vintage Camera", "Gaming PC"
        ]
        descriptions = [
            "High quality service with fast delivery.",
            "Detailed product in excellent condition.",
            "A standard package including all necessary files and support.",
            "Fully customized according to requirements.",
            "Premium quality offering with a 30-day money back guarantee."
        ]

        count = 10
        for i in range(count):
            receiver = random.choice(receivers)
            role = random.choice([Escrow.Role.SELLER, Escrow.Role.BUYER])
            item_type = random.choice([Escrow.ItemType.PRODUCT, Escrow.ItemType.SERVICE])
            product_name = random.choice(product_names) + f" {random.randint(100, 999)}"
            description = random.choice(descriptions)
            payment_option = random.choice([Escrow.PaymentOption.SINGLE, Escrow.PaymentOption.INSTALLMENT])
            status = random.choice(Escrow.Status.choices)[0]

            escrow = Escrow.objects.create(
                created_by=creator,
                receiver=receiver,
                role=role,
                item_type=item_type,
                product_name=product_name,
                description=description,
                payment_option=payment_option,
                status=status,
                currency="USD",
                fee_amount=Decimal('5.00')
            )

            if payment_option == Escrow.PaymentOption.SINGLE:
                escrow.price = Decimal(random.randint(50, 1000))
                escrow.total_amount = escrow.price + escrow.fee_amount
                escrow.save()
            else:
                num_installments = random.randint(2, 4)
                installment_amount = Decimal(random.randint(20, 250))
                for j in range(num_installments):
                    EscrowInstallment.objects.create(
                        escrow=escrow,
                        amount=installment_amount,
                        order=j + 1
                    )
                escrow.total_amount = (installment_amount * num_installments) + escrow.fee_amount
                escrow.save()

        self.stdout.write(self.style.SUCCESS(f'Successfully created {count} random escrows for hello@thatsayon.com'))
