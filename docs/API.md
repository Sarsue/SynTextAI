# Subscription API Documentation

## Base URL
All endpoints are prefixed with `/api/v1/subscriptions`

## Response Format
All endpoints return responses in the following JSON format:

```typescript
{
  "subscription_status": string;  // One of: 'active', 'trialing', 'canceled', 'past_due', 'unpaid', 'incomplete', 'incomplete_expired', 'none'
  "card_last4"?: string;          // Last 4 digits of the card
  "card_brand"?: string;          // Card brand (e.g., 'visa', 'mastercard')
  "card_exp_month"?: number;      // Card expiration month (1-12)
  "card_exp_year"?: number;       // Card expiration year (4 digits)
  "trial_end"?: string;           // ISO 8601 timestamp when trial ends (if applicable)
  "message"?: string;             // Optional success/status message
  "error"?: string;               // Present if status is 'error'
}
```

## Endpoints

### Get Subscription Status

**GET** `/status`

Get the current subscription status for the authenticated user.

#### Response

```json
{
  "subscription_status": "active",
  "card_last4": "4242",
  "card_brand": "visa",
  "card_exp_month": 12,
  "card_exp_year": 2025,
  "trial_end": "2023-12-31T23:59:59Z"
}
```

### Create Subscription

**POST** `/subscribe`

Create a new subscription or update an existing one.

#### Request Body

```typescript
{
  "payment_method_id": string;  // Stripe payment method ID
  "price_id"?: string;          // Optional: Stripe price ID (defaults to configured price)
}
```

#### Response

```json
{
  "subscription_status": "trialing",
  "card_last4": "4242",
  "card_brand": "visa",
  "card_exp_month": 12,
  "card_exp_year": 2025,
  "trial_end": "2023-12-31T23:59:59Z",
  "message": "Subscription created successfully"
}
```

### Update Payment Method

**POST** `/update-payment`

Update the payment method for the current subscription.

#### Request Body

```typescript
{
  "payment_method_id": string;  // New Stripe payment method ID
}
```

#### Response

```json
{
  "subscription_status": "active",
  "card_last4": "5555",
  "card_brand": "mastercard",
  "card_exp_month": 6,
  "card_exp_year": 2026,
  "message": "Payment method updated successfully"
}
```

### Cancel Subscription

**POST** `/cancel`

Cancel the current subscription at the end of the billing period.

#### Response

```json
{
  "subscription_status": "active",
  "card_last4": "4242",
  "card_brand": "visa",
  "card_exp_month": 12,
  "card_exp_year": 2025,
  "message": "Subscription will be canceled at the end of the billing period"
}
```

### Start Trial

**POST** `/start-trial`

Start a trial subscription for the user.

#### Request Body

```typescript
{
  "payment_method_id": string;  // Stripe payment method ID
}
```

#### Response

```json
{
  "subscription_status": "trialing",
  "card_last4": "4242",
  "card_brand": "visa",
  "card_exp_month": 12,
  "card_exp_year": 2025,
  "trial_end": "2023-12-31T23:59:59Z",
  "message": "Trial started successfully"
}
```

## Error Responses

All error responses follow the format:

```json
{
  "status": "error",
  "error": "Error message describing the issue"
}
```

Common error status codes:
- `400 Bad Request`: Invalid request data
- `401 Unauthorized`: Authentication required
- `403 Forbidden`: Insufficient permissions
- `404 Not Found`: Resource not found
- `409 Conflict`: Conflict with current state
- `500 Internal Server Error`: Server error
