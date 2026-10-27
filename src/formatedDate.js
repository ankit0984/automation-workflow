export function formattedDate() {
    // Get the current timestamp in milliseconds since January 1, 1970
    const timestamp = Date.now();
    
    // Create a JavaScript Date object from that timestamp
    const dateObject = new Date(timestamp);
    
    // Format it into a readable string (e.g., YYYY-MM-DD)
    return dateObject.toISOString().split('T')[0]; 
}

// Now call the function and log its returned value
console.log(formattedDate());

export function formattedYesterday() {
    // Get the current date
    const now = new Date();
    
    // Subtract one day from the current date
    now.setDate(now.getDate() - 1);
    
    // Format it into a "YYYY-MM-DD" string
    return now.toISOString().split('T')[0];
}

// Example usage:
console.log(formattedYesterday());
