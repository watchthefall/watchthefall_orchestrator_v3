/**
 * Brand Handler - Load and manage available brands for the portal
 */

class BrandHandler {
    constructor() {
        this.brands = [];
        this.loaded = false;
    }
    
    /**
     * Load available brands from the server
     */
    async loadBrands() {
        try {
            const response = await fetch('/api/brands/list');
            const data = await response.json();
            
            if (data.success) {
                // Extract brand names from the response
                this.brands = data.brands.map(brand => brand.name);
            } else {
                throw new Error(data.error || 'Failed to load brands');
            }
            
            this.loaded = true;
            return this.brands;
        } catch (error) {
            console.error('Error loading brands:', error);
            // Fallback to sample brands
            this.brands = [
                'ScotlandWTF', 'EnglandWTF', 'EuropeWTF', 'USAmericaWTF'
            ];
            this.loaded = true;
            return this.brands;
        }
    }
    
    /**
     * Get the list of available brands
     * @returns {Array} List of brand names
     */
    getBrands() {
        return [...this.brands]; // Return a copy
    }
}

// Export for use in other modules
window.BrandHandler = BrandHandler;