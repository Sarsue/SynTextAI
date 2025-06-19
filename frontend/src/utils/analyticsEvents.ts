// Analytics event types and utilities

export const AnalyticsEvents = {
  // Page views
  PAGE_VIEW: 'page_view',
  
  // User actions
  BUTTON_CLICK: 'button_click',
  LINK_CLICK: 'link_click',
  
  // Authentication
  USER_SIGNUP: 'user_signup',
  USER_LOGIN: 'user_login',
  USER_LOGOUT: 'user_logout',
  
  // Chat
  CHAT_MESSAGE_SENT: 'chat_message_sent',
  CHAT_MESSAGE_RECEIVED: 'chat_message_received',
  
  // Files
  FILE_UPLOAD: 'file_upload',
  FILE_DELETE: 'file_delete', // Generic delete, might be deprecated by specific ones below
  FILE_VIEW: 'file_view', // Generic view, might be deprecated by specific ones below
  FILE_DELETE_INITIATED: 'file_delete_initiated',
  FILE_DELETE_SUCCESS: 'file_delete_success',
  FILE_DELETE_FAILED: 'file_delete_failed',
  FILE_VIEW_CLICKED: 'file_view_clicked',
  FILE_VIEW_CLOSED: 'file_view_closed',
  YOUTUBE_LINK_SUBMITTED: 'youtube_link_submitted',
  
  // Knowledge Base
  KNOWLEDGE_BASE_SEARCH: 'knowledge_base_search',
  KNOWLEDGE_BASE_ADD: 'knowledge_base_add',
  
  // Errors
  ERROR: 'error',
  
  // Feature usage
  FEATURE_USAGE: 'feature_usage',
  
  // Navigation
  TAB_CHANGE: 'tab_change',
  
  // Settings
  SETTINGS_UPDATE: 'settings_update',
} as const;

export type AnalyticsEvent = keyof typeof AnalyticsEvents;

// Helper function to create consistent event properties
export const createEventProperties = (customProps: Record<string, any> = {}) => ({
  timestamp: new Date().toISOString(),
  url: window.location.href,
  path: window.location.pathname,
  ...customProps,
});
