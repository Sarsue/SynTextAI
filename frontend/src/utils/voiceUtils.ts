// Browser support and error handling
export const checkBrowserSupport = (): { supported: boolean; reason?: string } => {
    if (!('SpeechRecognition' in window || 'webkitSpeechRecognition' in window)) {
        return {
            supported: false,
            reason: 'Speech recognition is not supported in your browser. Please try Chrome, Edge, or Safari.'
        };
    }

    if (!('SpeechGrammarList' in window || 'webkitSpeechGrammarList' in window)) {
        return {
            supported: false,
            reason: 'Speech grammar is not supported in your browser.'
        };
    }

    return { supported: true };
};

// Voice commands handling
export const VOICE_COMMANDS = {
    CLEAR: ['clear message', 'clear all', 'start over'],
    SEND: ['send message', 'send now', 'submit'],
    NEW_LINE: ['new line', 'next line'],
    UNDO: ['undo that', 'undo last', 'remove last'],
    PAUSE: ['pause recording', 'pause'],
    RESUME: ['resume recording', 'resume'],
};

export const isVoiceCommand = (text: string): { isCommand: boolean; command?: string; type?: string } => {
    const lowerText = text.toLowerCase().trim();
    
    for (const [type, commands] of Object.entries(VOICE_COMMANDS)) {
        if (commands.some(cmd => lowerText === cmd)) {
            return { isCommand: true, command: lowerText, type };
        }
    }
    
    return { isCommand: false };
};

// Helper function to escape special characters in regex
const escapeRegExp = (string: string): string => {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); // $& means the whole matched string
};

// Profanity filter
const PROFANITY_REPLACEMENTS = new Map([
    ['***', '***'],
    // Add more profanity words and their replacements
]);

export const filterProfanity = (text: string): string => {
    let filteredText = text;
    PROFANITY_REPLACEMENTS.forEach((replacement, word) => {
        const escapedWord = escapeRegExp(word); // Escape the word here
        const regex = new RegExp(escapedWord, 'gi');
        filteredText = filteredText.replace(regex, replacement);
    });
    return filteredText;
};

// Speech rate detection
export const analyzeSpeechRate = (text: string, durationMs: number): { 
    wordsPerMinute: number;
    isTooFast: boolean 
} => {
    const words = text.trim().split(/\s+/).length;
    const minutes = durationMs / 60000;
    const wpm = words / minutes;
    
    return {
        wordsPerMinute: Math.round(wpm),
        isTooFast: wpm > 180 // Standard speaking rate is 120-160 wpm
    };
};

// Draft management
export const DRAFT_STORAGE_KEY = 'voice_input_drafts';

export interface VoiceDraft {
    id: string;
    text: string;
    timestamp: number;
}

export const saveDraft = (text: string): void => {
    const drafts: VoiceDraft[] = JSON.parse(localStorage.getItem(DRAFT_STORAGE_KEY) || '[]');
    drafts.push({
        id: Math.random().toString(36).substr(2, 9),
        text,
        timestamp: Date.now()
    });
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(drafts.slice(-5))); // Keep last 5 drafts
};

export const getDrafts = (): VoiceDraft[] => {
    return JSON.parse(localStorage.getItem(DRAFT_STORAGE_KEY) || '[]');
};

export const clearDraft = (id: string): void => {
    const drafts: VoiceDraft[] = JSON.parse(localStorage.getItem(DRAFT_STORAGE_KEY) || '[]');
    localStorage.setItem(
        DRAFT_STORAGE_KEY,
        JSON.stringify(drafts.filter(draft => draft.id !== id))
    );
};
