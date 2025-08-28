import asyncio
from sqlalchemy import select
from api.models.orm_models import Subscription, CardDetails
from api.core.database import async_session

async def check_subscriptions():
    async with async_session() as session:
        # Query all subscriptions with their card details
        result = await session.execute(
            select(Subscription, CardDetails)
            .join(CardDetails, Subscription.id == CardDetails.subscription_id, isouter=True)
        )
        
        subscriptions = result.all()
        
        if not subscriptions:
            print("No subscriptions found in the database.")
            return
            
        print(f"Found {len(subscriptions)} subscription(s):")
        for sub, card in subscriptions:
            print(f"\nSubscription ID: {sub.id}")
            print(f"User ID: {sub.user_id}")
            print(f"Status: {sub.status}")
            print(f"Created At: {sub.created_at}")
            
            if card:
                print("\nCard Details:")
                print(f"  Last 4: {card.card_last4}")
                print(f"  Type: {card.card_type}")
                print(f"  Exp: {card.exp_month}/{card.exp_year}")
            else:
                print("No card details found for this subscription.")

if __name__ == "__main__":
    asyncio.run(check_subscriptions())
