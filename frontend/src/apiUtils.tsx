// apiUtils.ts


export const LogUIActions = async (url: string, method: string, message: string, level: string = 'info') => {
    try {
        const payload = JSON.stringify({
            level: level.toLowerCase(), // Convert to lowercase if needed
            message: message,
            timestamp: new Date().toISOString(), // Add a timestamp
        });

        const response = await fetch(url, {
            method,
            headers: {
                'Content-Type': 'application/json',
            },
            mode: 'cors',
            body: payload,
        });

        return response;
    } catch (error) {
        console.error('Unexpected error logging UI action:', error);
        return null;
    }
};
