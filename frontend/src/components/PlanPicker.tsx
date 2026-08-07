/**
 * The plans, and the buttons that choose one.
 *
 * Shared by settings and by signup, which both have to show the same prices.
 * Two copies of this would eventually advertise two different numbers, and the
 * one on the screen nobody looks at would be the wrong one.
 *
 * Prices come from the backend, which derives them from the same plan
 * definition the Stripe prices were created from. Hardcoding them in a
 * component is how a page ends up advertising a price the customer is not
 * charged.
 */
import React, { useEffect, useState } from 'react';

export interface PlanOption {
    key: string;
    name: string;
    description: string;
    base_cents: number;
    included_seats: number;
    overage_cents: number;
    available: boolean;
}

/** The plans on offer, and the first one preselected once they arrive. */
export function usePlans(onFirstLoaded?: (key: string) => void) {
    const [plans, setPlans] = useState<PlanOption[]>([]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const response = await fetch('/api/v1/subscriptions/plans');
                if (!response.ok) return;
                const data = await response.json();
                if (cancelled || !Array.isArray(data?.plans)) return;
                const available = data.plans.filter((p: PlanOption) => p.available);
                setPlans(available);
                if (available.length) onFirstLoaded?.(available[0].key);
            } catch {
                // Leaves the picker empty and the submit button disabled, which
                // is better than offering a plan we cannot charge for.
            }
        })();
        return () => { cancelled = true; };
        // Runs once. onFirstLoaded is called at most once, on arrival.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return plans;
}

interface PlanChoicesProps {
    plans: PlanOption[];
    selected: string;
    onSelect: (key: string) => void;
}

export const PlanChoices: React.FC<PlanChoicesProps> = ({ plans, selected, onSelect }) => (
    <div className="plan-choices">
        {plans.map((plan) => {
            const isSelected = plan.key === selected;
            return (
                <button
                    type="button"
                    key={plan.key}
                    onClick={() => onSelect(plan.key)}
                    disabled={!plan.available}
                    aria-pressed={isSelected}
                    className={`plan-choice ${isSelected ? 'is-selected' : ''}`}
                >
                    <span className="plan-choice-name">{plan.name}</span>
                    <span className="plan-choice-price">
                        ${(plan.base_cents / 100).toFixed(0)}
                        <span className="plan-choice-period">/month</span>
                    </span>
                    <span className="plan-choice-seats">
                        {plan.included_seats} seats included, then $
                        {(plan.overage_cents / 100).toFixed(0)} each
                    </span>
                    <span className="plan-choice-description">{plan.description}</span>
                </button>
            );
        })}
    </div>
);
