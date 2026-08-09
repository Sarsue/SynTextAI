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

    /**
     * This caller's own rating of this answer, or null if they have not rated
     * it. Replaces a `liked`/`disliked` pair that nothing ever rendered and
     * that the API never populated: it was read as `m.is_liked === 1` against
     * a field the server does not send, so it was permanently false.
     */
    feedback?: MessageFeedback | null;

}

/** A chip from the thumbs-down form. The backend validates the set. */
export type FeedbackReason = 'wrong' | 'incomplete' | 'not_in_documents' | 'wrong_source';

export interface MessageFeedback {
    rating: 1 | -1;
    reason?: FeedbackReason | null;
    comment?: string | null;
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