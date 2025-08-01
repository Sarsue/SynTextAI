import React, { Component, ErrorInfo, ReactNode, ComponentType } from 'react';

interface FallbackProps {
  error: Error;
  resetErrorBoundary: () => void;
}

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  FallbackComponent?: ComponentType<FallbackProps>;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface State {
  hasError: boolean;
  error?: Error;
  errorInfo?: ErrorInfo;
}

class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Uncaught error:', error, errorInfo);
    this.setState({ errorInfo });
    this.props.onError?.(error, errorInfo);
  }

  private handleRetry = () => {
    this.setState({ hasError: false, error: undefined, errorInfo: undefined });
  };

  public render() {
    const { fallback, FallbackComponent, children } = this.props;
    const { hasError, error, errorInfo } = this.state;

    if (hasError && error) {
      // If FallbackComponent is provided, use it
      if (FallbackComponent) {
        return <FallbackComponent error={error} resetErrorBoundary={this.handleRetry} />;
      }
      
      // Otherwise, use the fallback prop or default UI
      if (fallback) {
        return <>{fallback}</>;
      }

      // Default error UI
      return (
        <div className="error-boundary" style={{
          padding: '1rem',
          border: '1px solid #ff6b6b',
          borderRadius: '4px',
          backgroundColor: '#fff5f5',
          color: '#c92a2a',
          maxWidth: '600px',
          margin: '2rem auto'
        }}>
          <h2 style={{ marginTop: 0 }}>Something went wrong</h2>
          <details style={{ marginBottom: '1rem' }}>
            <summary>Error details</summary>
            <pre style={{ 
              whiteSpace: 'pre-wrap',
              backgroundColor: 'rgba(0,0,0,0.05)',
              padding: '0.5rem',
              borderRadius: '4px',
              maxHeight: '200px',
              overflow: 'auto'
            }}>
              {error.toString()}
              {errorInfo?.componentStack}
            </pre>
          </details>
          <button 
            onClick={this.handleRetry}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: '#339af0',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              marginRight: '0.5rem'
            }}
          >
            Try again
          </button>
          <button 
            onClick={() => window.location.reload()}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: '#f8f9fa',
              color: '#495057',
              border: '1px solid #dee2e6',
              borderRadius: '4px',
              cursor: 'pointer'
            }}
          >
            Reload page
          </button>
        </div>
      );
    }

    return <>{children}</>;
  }
}

export { ErrorBoundary };
export type { FallbackProps };
