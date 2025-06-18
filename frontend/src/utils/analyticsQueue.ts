import { AnalyticsEvents } from './analyticsEvents';

const MAX_RETRIES = 3;
const RETRY_DELAY = 1000; // 1 second

/**
 * Queue for critical analytics events that should be retried on failure
 */
class AnalyticsQueue {
  private static instance: AnalyticsQueue;
  private queue: Array<() => Promise<void>> = [];
  private isProcessing = false;

  private constructor() {}

  public static getInstance(): AnalyticsQueue {
    if (!AnalyticsQueue.instance) {
      AnalyticsQueue.instance = new AnalyticsQueue();
    }
    return AnalyticsQueue.instance;
  }

  public enqueue(
    action: () => Promise<void>,
    eventName: string,
    properties: Record<string, any> = {}
  ): void {
    const task = async (retryCount = 0): Promise<void> => {
      try {
        await action();
        // Log success in development
        if (process.env.NODE_ENV === 'development') {
          console.log(`[Analytics] Event sent: ${eventName}`, properties);
        }
      } catch (error) {
        if (retryCount < MAX_RETRIES) {
          // Exponential backoff
          const delay = RETRY_DELAY * Math.pow(2, retryCount);
          if (process.env.NODE_ENV === 'development') {
            console.warn(
              `[Analytics] Retry ${retryCount + 1}/${MAX_RETRIES} for ${eventName} in ${delay}ms`,
              error
            );
          }
          
          await new Promise(resolve => setTimeout(resolve, delay));
          return task(retryCount + 1);
        }
        
        // Log final failure
        console.error(`[Analytics] Failed to send event after ${MAX_RETRIES} retries:`, {
          event: eventName,
          error,
          properties,
        });
      }
    };

    this.queue.push(() => task(0));
    this.processQueue();
  }

  private async processQueue(): Promise<void> {
    if (this.isProcessing || this.queue.length === 0) {
      return;
    }

    this.isProcessing = true;
    const task = this.queue.shift();
    
    if (task) {
      try {
        await task();
      } finally {
        this.isProcessing = false;
        this.processQueue(); // Process next item in queue
      }
    } else {
      this.isProcessing = false;
    }
  }
}

// Type for the posthog object we expect to use
type PostHogClient = {
  capture: (event: string, properties?: Record<string, any>) => void | Promise<any>;
  reset?: () => void;
  identify?: (userId: string, traits?: Record<string, any>) => void;
  flush?: () => void | Promise<void>;
  [key: string]: any; // Allow any other properties
};

// Get the posthog instance with proper typing
export const getPosthog = (): PostHogClient | undefined => {
  if (typeof window !== 'undefined') {
    return (window as any).posthog as PostHogClient;
  }
  return undefined;
};

/**
 * Track a critical analytics event with retry logic
 */
export const trackCritical = (
  eventName: string,
  properties: Record<string, any> = {}
): void => {
  const posthog = getPosthog();
  if (!posthog) {
    if (process.env.NODE_ENV === 'development') {
      console.warn('[Analytics] PostHog not available - event not queued:', eventName);
    }
    return;
  }

  const queue = AnalyticsQueue.getInstance();
  queue.enqueue(
    () => posthog.capture(eventName, properties) || Promise.resolve(),
    eventName,
    properties
  );
};

/**
 * Track a page view with retry logic
 */
export const trackPageView = (pageName: string, properties: Record<string, any> = {}): void => {
  const posthog = getPosthog();
  if (posthog) {
    trackCritical(AnalyticsEvents.PAGE_VIEW, {
      page_name: pageName,
      ...properties,
    });
  } else if (process.env.NODE_ENV === 'development') {
    console.warn('[Analytics] Page view not tracked - PostHog not available:', pageName);
  }
};

/**
 * Track a user action with retry logic
 */
export const trackAction = (
  action: string,
  category: string,
  label?: string,
  value?: number
): void => {
  const posthog = getPosthog();
  if (posthog) {
    trackCritical(AnalyticsEvents.BUTTON_CLICK, {
      action,
      category,
      ...(label && { label }),
      ...(value !== undefined && { value }),
    });
  } else if (process.env.NODE_ENV === 'development') {
    console.warn('[Analytics] Action not tracked - PostHog not available:', { action, category });
  }
};

/**
 * Track an error with retry logic
 */
export const trackError = (error: Error, context: Record<string, any> = {}): void => {
  const posthog = getPosthog();
  if (posthog) {
    trackCritical(AnalyticsEvents.ERROR, {
      error_message: error.message,
      error_name: error.name,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined,
      ...context,
    });
  } else if (process.env.NODE_ENV === 'development') {
    console.error('Error tracking failed - PostHog not available:', {
      error: error.message,
      context,
    });
  }
};
