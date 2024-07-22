/// <reference types="react-scripts" />
/// <reference types="react-scripts" />

declare namespace NodeJS {
    interface ProcessEnv {
        readonly REACT_APP_STRIPE_API_KEY: string;
        readonly REACT_APP_API_BASE_URL: string
    }
}
