// Aligned with MAX_CONTENT_LENGTH (docker/.env + Helm global.maxContentLength).
// If the server-side value changes, change it here too — the server remains the
// authority (returns 413), this is only a client-side pre-flight guard so users
// get an immediate, friendly rejection instead of waiting on a failed upload.
export const MAX_UPLOAD_FILE_SIZE_BYTES = 1024 * 1024 * 1024;
export const MAX_UPLOAD_FILE_SIZE_LABEL = '1 Go';
