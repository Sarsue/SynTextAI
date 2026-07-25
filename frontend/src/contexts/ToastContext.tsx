import React, { createContext, useContext, useCallback, ReactNode } from 'react';
import { toast } from 'sonner';
import { Toaster } from '@/components/ui/sonner';

export type ToastType = 'success' | 'error' | 'info' | 'warning';

interface ToastContextType {
  addToast: (message: string, type: ToastType, duration?: number) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export const useToast = (): ToastContextType => {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
};

export const ToastProvider: React.FC<{children: ReactNode}> = ({ children }) => {
  const addToast = useCallback((message: string, type: ToastType, duration: number = 5000) => {
    toast[type](message, { duration });
  }, []);

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      <Toaster position="top-right" />
    </ToastContext.Provider>
  );
};
