// apiUtils.ts


export const LogUIActions = async (url: string, method: string, message: string, level: string) => {
    try {
        const response = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ level, message, timestamp: new Date().toISOString() }),
        });

        if (!response.ok) {
            console.error(`Failed to log action: ${response.statusText}`);
        }
    } catch (error) {
        console.error('Error logging UI action:', error);
    }
};
