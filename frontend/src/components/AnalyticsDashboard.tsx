import React, { useState, useEffect } from 'react';
import { Chart as ChartJS, ArcElement, Tooltip, Legend, CategoryScale, LinearScale, PointElement, LineElement, BarElement, Title } from 'chart.js';
import { Pie, Line, Bar } from 'react-chartjs-2';

// Register ChartJS components
ChartJS.register(
  ArcElement, 
  Tooltip, 
  Legend,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title
);

interface DashboardProps {
  startDate?: Date;
  endDate?: Date;
  filter?: string;
}

interface AnalyticsMetrics {
  totalSessions: number;
  activeUsers: number;
  averageSessionDuration: number;
  bounceRate: number;
  clickEvents: number;
  rageClicks: number;
  ghostedSessions: number;
  [key: string]: number;
}

interface AnalyticsDashboardData {
  status: string;
  data: {
    metrics: AnalyticsMetrics;
    clickHeatmap?: Record<string, number>;
    exitPathsData?: Record<string, number>;
    engagementOverTime?: {
      labels: string[];
      datasets: {
        label: string;
        data: number[];
        backgroundColor: string;
        borderColor: string;
      }[];
    };
    userFlowData?: {
      from: string;
      to: string;
      value: number;
    }[];
  };
}

const AnalyticsDashboard: React.FC<DashboardProps> = ({ startDate, endDate, filter }) => {
  const [dashboardData, setDashboardData] = useState<AnalyticsDashboardData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  
  useEffect(() => {
    const fetchAnalyticsData = async () => {
      setLoading(true);
      try {
        // Build query parameters
        const params = new URLSearchParams();
        if (startDate) params.append('start_date', startDate.toISOString());
        if (endDate) params.append('end_date', endDate.toISOString());
        if (filter) params.append('filter', filter);
        
        // Fetch data from API
        const response = await fetch(`/api/analytics/dashboard?${params.toString()}`);
        
        if (!response.ok) {
          throw new Error(`Error fetching analytics: ${response.statusText}`);
        }
        
        const data = await response.json();
        setDashboardData(data);
      } catch (err) {
        setError(`Failed to load analytics: ${err instanceof Error ? err.message : String(err)}`);
        console.error('Analytics error:', err);
        
        // For demo purposes, create mock data if API fails
        setDashboardData(getMockData());
      } finally {
        setLoading(false);
      }
    };
    
    fetchAnalyticsData();
  }, [startDate, endDate, filter]);
  
  if (loading) {
    return <div className="analytics-loading">Loading analytics data...</div>;
  }
  
  if (error && !dashboardData) {
    return <div className="analytics-error">Error: {error}</div>;
  }
  
  return (
    <div className="analytics-dashboard">
      <h1>User Behavior Analytics</h1>
      
      {dashboardData && (
        <>
          <div className="metrics-overview">
            <h2>Overview</h2>
            <div className="metrics-grid">
              <MetricCard 
                title="Active Users" 
                value={dashboardData.data.metrics.activeUsers} 
                icon="👤"
              />
              <MetricCard 
                title="Avg. Session" 
                value={`${Math.round(dashboardData.data.metrics.averageSessionDuration / 60)}m`}
                icon="⏱️" 
              />
              <MetricCard 
                title="Bounce Rate" 
                value={`${dashboardData.data.metrics.bounceRate}%`}
                icon="↩️" 
              />
              <MetricCard 
                title="Click Events" 
                value={dashboardData.data.metrics.clickEvents}
                icon="👆" 
              />
              <MetricCard 
                title="Rage Clicks" 
                value={dashboardData.data.metrics.rageClicks}
                icon="😡" 
              />
              <MetricCard 
                title="Ghosted Sessions" 
                value={dashboardData.data.metrics.ghostedSessions}
                icon="👻" 
              />
            </div>
          </div>
          
          <div className="charts-row">
            <div className="chart-container">
              <h3>Exit Types</h3>
              <Pie 
                data={{
                  labels: ['Normal Exit', 'Rage Quit', 'Ghost (Timeout)'],
                  datasets: [
                    {
                      data: [
                        dashboardData.data.metrics.totalSessions - 
                          dashboardData.data.metrics.rageClicks - 
                          dashboardData.data.metrics.ghostedSessions,
                        dashboardData.data.metrics.rageClicks,
                        dashboardData.data.metrics.ghostedSessions
                      ],
                      backgroundColor: [
                        'rgba(75, 192, 192, 0.6)',
                        'rgba(255, 99, 132, 0.6)',
                        'rgba(153, 102, 255, 0.6)',
                      ],
                      borderColor: [
                        'rgba(75, 192, 192, 1)',
                        'rgba(255, 99, 132, 1)',
                        'rgba(153, 102, 255, 1)',
                      ],
                      borderWidth: 1,
                    },
                  ],
                }}
                options={{
                  plugins: {
                    legend: {
                      position: 'bottom',
                    },
                    tooltip: {
                      callbacks: {
                        label: function(context) {
                          const label = context.label || '';
                          const value = context.raw;
                          const total = context.dataset.data.reduce((a: number, b: number) => a + b, 0);
                          const percentage = Math.round((value as number / total) * 100);
                          return `${label}: ${value} (${percentage}%)`;
                        }
                      }
                    }
                  }
                }}
              />
            </div>
            
            <div className="chart-container">
              <h3>Engagement Over Time</h3>
              <Line 
                data={dashboardData.data.engagementOverTime || {
                  labels: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
                  datasets: [
                    {
                      label: 'Page Views',
                      data: [65, 78, 52, 91, 85, 36, 47],
                      borderColor: 'rgba(75, 192, 192, 1)',
                      backgroundColor: 'rgba(75, 192, 192, 0.2)',
                    },
                    {
                      label: 'User Clicks',
                      data: [28, 48, 40, 69, 76, 27, 31],
                      borderColor: 'rgba(255, 99, 132, 1)',
                      backgroundColor: 'rgba(255, 99, 132, 0.2)',
                    }
                  ]
                }}
                options={{
                  responsive: true,
                  plugins: {
                    legend: {
                      position: 'top',
                    },
                    title: {
                      display: false,
                    },
                  },
                  scales: {
                    y: {
                      beginAtZero: true,
                    },
                  },
                }}
              />
            </div>
          </div>
          
          <div className="charts-row">
            <div className="chart-container">
              <h3>Top Exit Pages</h3>
              <Bar
                data={{
                  labels: Object.keys(dashboardData.data.exitPathsData || {
                    '/': 45,
                    '/chat': 28,
                    '/settings': 17,
                    '/login': 10,
                  }),
                  datasets: [
                    {
                      label: 'Exit Count',
                      data: Object.values(dashboardData.data.exitPathsData || {
                        '/': 45,
                        '/chat': 28,
                        '/settings': 17,
                        '/login': 10,
                      }),
                      backgroundColor: 'rgba(153, 102, 255, 0.5)',
                      borderColor: 'rgba(153, 102, 255, 1)',
                      borderWidth: 1,
                    }
                  ]
                }}
                options={{
                  indexAxis: 'y',
                  scales: {
                    x: {
                      beginAtZero: true,
                      title: {
                        display: true,
                        text: 'Number of exits'
                      }
                    },
                    y: {
                      title: {
                        display: true,
                        text: 'Page path'
                      }
                    }
                  },
                }}
              />
            </div>
            
            <div className="chart-container">
              <h3>Engagement Funnel</h3>
              <Bar
                data={{
                  labels: ['View Home', 'Start Chat', 'Complete Chat', 'Return Visit'],
                  datasets: [
                    {
                      label: 'Users',
                      data: [100, 67, 42, 31],
                      backgroundColor: 'rgba(54, 162, 235, 0.5)',
                      borderColor: 'rgba(54, 162, 235, 1)',
                      borderWidth: 1,
                    }
                  ]
                }}
                options={{
                  scales: {
                    y: {
                      beginAtZero: true,
                      title: {
                        display: true,
                        text: 'Percentage of users'
                      }
                    }
                  },
                }}
              />
            </div>
          </div>
          
          <div className="rage-ghost-section">
            <h2>User Frustration Insights</h2>
            <div className="insight-cards">
              <div className="insight-card rage">
                <h4>Rage Click Hotspots</h4>
                <p>
                  <strong>Top frustration points:</strong>
                </p>
                <ul>
                  {Object.entries(dashboardData.data.clickHeatmap || {
                    'chat > send-button': 43,
                    'settings > save-button': 28,
                    'login > submit-form': 19
                  }).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([element, count], idx) => (
                    <li key={idx}><code>{element}</code>: {count} rage clicks</li>
                  ))}
                </ul>
                <div className="insight-tip">
                  <strong>Tip:</strong> Consider improving UI feedback for these elements
                </div>
              </div>
              
              <div className="insight-card ghost">
                <h4>Ghost Abandonment</h4>
                <p>
                  <strong>Pages where users commonly ghost:</strong>
                </p>
                <ul>
                  <li>Chat page: 62% of ghosting</li>
                  <li>Settings page: 27% of ghosting</li>
                  <li>Login form: 11% of ghosting</li>
                </ul>
                <div className="insight-tip">
                  <strong>Tip:</strong> Add progress indicators and save states
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

interface MetricCardProps {
  title: string;
  value: string | number;
  icon?: string;
  trend?: number;
}

const MetricCard: React.FC<MetricCardProps> = ({ title, value, icon, trend }) => {
  return (
    <div className="metric-card">
      {icon && <span className="metric-icon">{icon}</span>}
      <div className="metric-info">
        <h3 className="metric-title">{title}</h3>
        <div className="metric-value">{value}</div>
        {trend !== undefined && (
          <div className={`metric-trend ${trend > 0 ? 'positive' : trend < 0 ? 'negative' : ''}`}>
            {trend > 0 ? '↑' : trend < 0 ? '↓' : '→'} {Math.abs(trend)}%
          </div>
        )}
      </div>
    </div>
  );
};

// Mock data generator for testing
const getMockData = (): AnalyticsDashboardData => {
  return {
    status: 'success',
    data: {
      metrics: {
        totalSessions: 427,
        activeUsers: 312,
        averageSessionDuration: 486, // in seconds
        bounceRate: 24,
        clickEvents: 5834,
        rageClicks: 87,
        ghostedSessions: 53
      },
      clickHeatmap: {
        'chat > send-button': 43,
        'settings > save-button': 28,
        'login > submit-form': 19,
        'home > start-button': 14,
        'chat > file-upload': 9,
      },
      exitPathsData: {
        '/': 45,
        '/chat': 28,
        '/settings': 17,
        '/login': 10,
      },
      engagementOverTime: {
        labels: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
        datasets: [
          {
            label: 'Page Views',
            data: [65, 78, 52, 91, 85, 36, 47],
            backgroundColor: 'rgba(75, 192, 192, 0.2)',
            borderColor: 'rgba(75, 192, 192, 1)',
          },
          {
            label: 'User Clicks',
            data: [28, 48, 40, 69, 76, 27, 31],
            backgroundColor: 'rgba(255, 99, 132, 0.2)',
            borderColor: 'rgba(255, 99, 132, 1)',
          }
        ]
      },
    }
  };
};

export default AnalyticsDashboard;
