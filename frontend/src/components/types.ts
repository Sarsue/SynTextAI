

export interface DocumentInfo {
    id: number;
    title: string;
    type: 'pdf' | 'image' | 'audio' | 'video' | 'text';
    path: string;
}

export interface User {
    uid: string;
    displayName: string | null;
    email: string | null;

}
export interface UploadedFile {
    id: number;
    name: string;
    publicUrl: string;
    processed: boolean;
    summary: string | null;
}
export interface Explanation {
    id: number;
    file_id: number;
    user_id: number;
    context_info: string | null;
    explanation_text: string | null; // Allow null
    created_at: string;
    selection_type: string;
    page: number | null;
    video_start?: number | null;
    video_end?: number | null;
}
export interface Message {

    id: number;

    content: string;

    sender: 'user' | 'bot';

    timestamp: string;

    liked: boolean;

    disliked: boolean;

}
export interface History {
    id: number;
    title: string;
    messages: Message[];
}

export interface Persona {
    id: number;
    name: string;
}

