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

export type ProcessingStatus =
  | 'uploaded'
  | 'extracting'
  | 'embedding'
  | 'storing'
  | 'processed'
  | 'failed';

export interface UploadedFile {
    id: number;
    file_name: string;
    file_url: string;
    created_at?: string;
    file_type: 'pdf' | 'image' | 'audio' | 'video' | 'text';
    status: ProcessingStatus; // Current processing state

}

export interface KeyConcept {
    id: number;
    file_id: number;
    concept_title: string | null;
    concept_explanation: string | null;
    display_order: number | null;
    source_page_number: number | null;
    created_at: string;
    is_custom: boolean;
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

export interface PaginationState {
    page: number;
    pageSize: number;
    totalItems: number;
}