// Type definitions for PostHog

type PostHog = {
  capture: (event: string, properties?: Record<string, any>) => void | Promise<any>;
  reset: () => void;
  identify: (userId: string, traits?: Record<string, any>) => void;
  flush: () => Promise<void> | void;
  [key: string]: any;
};

declare global {
  interface Window {
    posthog?: PostHog;
  }
}

export {};
