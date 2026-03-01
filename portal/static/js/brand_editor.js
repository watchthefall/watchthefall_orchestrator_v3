/**
 * Brand Creator Visual Editor
 * Drag-and-drop overlay editor for logo, watermark, and text positioning
 */

class BrandEditor {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) {
            console.error(`Canvas ${canvasId} not found`);
            return;
        }
        
        this.ctx = this.canvas.getContext('2d');
        
        // Canvas dimensions (9:16 ratio - 1080x1920 logical)
        this.CANVAS_WIDTH = 540;
        this.CANVAS_HEIGHT = 960;
        this.canvas.width = this.CANVAS_WIDTH;
        this.canvas.height = this.CANVAS_HEIGHT;
        
        // Editor state
        this.state = {
            // Logo
            logoFile: null,
            logoImg: null,
            logoX: 0.85,  // percent (0-1)
            logoY: 0.85,
            logoScale: 0.15,
            logoOpacity: 1.0,
            
            // Watermark
            watermarkFile: null,
            watermarkImg: null,
            wmMode: 'fullscreen',
            wmX: 0.5,
            wmY: 0.5,
            wmScale: 1.0,
            wmOpacity: 0.10,
            
            // Text
            textEnabled: false,
            textContent: '',
            textX: 0.5,
            textY: 0.2,
            textSize: 48,
            textColor: '#FFFFFF'
        };
        
        // Drag state
        this.dragging = null; // 'logo', 'watermark', 'text', or null
        this.dragStart = { x: 0, y: 0 };
        
        // Setup event listeners
        this.setupEventListeners();
        
        // Initial render
        this.render();
    }
    
    setupEventListeners() {
        // Mouse events for dragging
        this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
        this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.canvas.addEventListener('mouseup', () => this.handleMouseUp());
        this.canvas.addEventListener('mouseleave', () => this.handleMouseUp());
        
        // Touch events for mobile
        this.canvas.addEventListener('touchstart', (e) => this.handleTouchStart(e));
        this.canvas.addEventListener('touchmove', (e) => this.handleTouchMove(e));
        this.canvas.addEventListener('touchend', () => this.handleMouseUp());
    }
    
    // Get mouse position relative to canvas (as percent 0-1)
    getMousePos(e) {
        const rect = this.canvas.getBoundingClientRect();
        const scaleX = this.canvas.width / rect.width;
        const scaleY = this.canvas.height / rect.height;
        
        const x = (e.clientX - rect.left) * scaleX;
        const y = (e.clientY - rect.top) * scaleY;
        
        return {
            x: x / this.CANVAS_WIDTH,
            y: y / this.CANVAS_HEIGHT
        };
    }
    
    handleMouseDown(e) {
        const pos = this.getMousePos(e);
        
        // Check what was clicked (priority: text > logo > watermark)
        if (this.state.textEnabled && this.isOverText(pos)) {
            this.dragging = 'text';
            this.dragStart = { x: pos.x - this.state.textX, y: pos.y - this.state.textY };
        } else if (this.state.logoImg && this.isOverLogo(pos)) {
            this.dragging = 'logo';
            this.dragStart = { x: pos.x - this.state.logoX, y: pos.y - this.state.logoY };
        } else if (this.state.watermarkImg && this.state.wmMode === 'positioned' && this.isOverWatermark(pos)) {
            this.dragging = 'watermark';
            this.dragStart = { x: pos.x - this.state.wmX, y: pos.y - this.state.wmY };
        }
    }
    
    handleMouseMove(e) {
        if (!this.dragging) {
            // Update cursor
            const pos = this.getMousePos(e);
            if ((this.state.textEnabled && this.isOverText(pos)) ||
                (this.state.logoImg && this.isOverLogo(pos)) ||
                (this.state.watermarkImg && this.state.wmMode === 'positioned' && this.isOverWatermark(pos))) {
                this.canvas.style.cursor = 'move';
            } else {
                this.canvas.style.cursor = 'default';
            }
            return;
        }
        
        const pos = this.getMousePos(e);
        
        if (this.dragging === 'logo') {
            this.state.logoX = Math.max(0, Math.min(1, pos.x - this.dragStart.x));
            this.state.logoY = Math.max(0, Math.min(1, pos.y - this.dragStart.y));
        } else if (this.dragging === 'watermark') {
            this.state.wmX = Math.max(0, Math.min(1, pos.x - this.dragStart.x));
            this.state.wmY = Math.max(0, Math.min(1, pos.y - this.dragStart.y));
        } else if (this.dragging === 'text') {
            this.state.textX = Math.max(0, Math.min(1, pos.x - this.dragStart.x));
            this.state.textY = Math.max(0, Math.min(1, pos.y - this.dragStart.y));
        }
        
        this.render();
    }
    
    handleMouseUp() {
        this.dragging = null;
        this.canvas.style.cursor = 'default';
    }
    
    handleTouchStart(e) {
        e.preventDefault();
        if (e.touches.length > 0) {
            const touch = e.touches[0];
            this.handleMouseDown({ clientX: touch.clientX, clientY: touch.clientY });
        }
    }
    
    handleTouchMove(e) {
        e.preventDefault();
        if (e.touches.length > 0) {
            const touch = e.touches[0];
            this.handleMouseMove({ clientX: touch.clientX, clientY: touch.clientY });
        }
    }
    
    // Hit detection
    isOverLogo(pos) {
        if (!this.state.logoImg) return false;
        const size = this.state.logoScale * this.CANVAS_WIDTH;
        const x = this.state.logoX * this.CANVAS_WIDTH;
        const y = this.state.logoY * this.CANVAS_HEIGHT;
        return pos.x * this.CANVAS_WIDTH >= x - size/2 && 
               pos.x * this.CANVAS_WIDTH <= x + size/2 &&
               pos.y * this.CANVAS_HEIGHT >= y - size/2 && 
               pos.y * this.CANVAS_HEIGHT <= y + size/2;
    }
    
    isOverWatermark(pos) {
        if (!this.state.watermarkImg || this.state.wmMode !== 'positioned') return false;
        const size = this.state.wmScale * this.CANVAS_WIDTH * 0.5;
        const x = this.state.wmX * this.CANVAS_WIDTH;
        const y = this.state.wmY * this.CANVAS_HEIGHT;
        return pos.x * this.CANVAS_WIDTH >= x - size/2 && 
               pos.x * this.CANVAS_WIDTH <= x + size/2 &&
               pos.y * this.CANVAS_HEIGHT >= y - size/2 && 
               pos.y * this.CANVAS_HEIGHT <= y + size/2;
    }
    
    isOverText(pos) {
        if (!this.state.textEnabled || !this.state.textContent) return false;
        const x = this.state.textX * this.CANVAS_WIDTH;
        const y = this.state.textY * this.CANVAS_HEIGHT;
        const size = this.state.textSize * 2;
        return pos.x * this.CANVAS_WIDTH >= x - size/2 && 
               pos.x * this.CANVAS_WIDTH <= x + size/2 &&
               pos.y * this.CANVAS_HEIGHT >= y - size/2 && 
               pos.y * this.CANVAS_HEIGHT <= y + size/2;
    }
    
    // Render canvas
    render() {
        // Clear
        this.ctx.fillStyle = '#1a1a2e';
        this.ctx.fillRect(0, 0, this.CANVAS_WIDTH, this.CANVAS_HEIGHT);
        
        // Draw gradient background
        const gradient = this.ctx.createLinearGradient(0, 0, 0, this.CANVAS_HEIGHT);
        gradient.addColorStop(0, '#1a1a2e');
        gradient.addColorStop(1, '#16213e');
        this.ctx.fillStyle = gradient;
        this.ctx.fillRect(0, 0, this.CANVAS_WIDTH, this.CANVAS_HEIGHT);
        
        // Draw watermark (behind logo)
        if (this.state.watermarkImg) {
            this.drawWatermark();
        }
        
        // Draw logo
        if (this.state.logoImg) {
            this.drawLogo();
        }
        
        // Draw text overlay
        if (this.state.textEnabled && this.state.textContent) {
            this.drawText();
        }
    }
    
    drawWatermark() {
        const img = this.state.watermarkImg;
        this.ctx.globalAlpha = this.state.wmOpacity;
        
        if (this.state.wmMode === 'fullscreen') {
            // Centered, scaled to fit
            const scale = Math.min(
                (this.CANVAS_WIDTH / img.width) * this.state.wmScale,
                (this.CANVAS_HEIGHT / img.height) * this.state.wmScale
            );
            const w = img.width * scale;
            const h = img.height * scale;
            const x = (this.CANVAS_WIDTH - w) / 2;
            const y = (this.CANVAS_HEIGHT - h) / 2;
            this.ctx.drawImage(img, x, y, w, h);
        } else {
            // Positioned mode - draggable
            const size = this.state.wmScale * this.CANVAS_WIDTH * 0.5;
            const x = this.state.wmX * this.CANVAS_WIDTH;
            const y = this.state.wmY * this.CANVAS_HEIGHT;
            this.ctx.drawImage(img, x - size/2, y - size/2, size, size);
        }
        
        this.ctx.globalAlpha = 1.0;
    }
    
    drawLogo() {
        const img = this.state.logoImg;
        this.ctx.globalAlpha = this.state.logoOpacity;
        
        const size = this.state.logoScale * this.CANVAS_WIDTH;
        const x = this.state.logoX * this.CANVAS_WIDTH;
        const y = this.state.logoY * this.CANVAS_HEIGHT;
        
        this.ctx.drawImage(img, x - size/2, y - size/2, size, size);
        this.ctx.globalAlpha = 1.0;
    }
    
    drawText() {
        const x = this.state.textX * this.CANVAS_WIDTH;
        const y = this.state.textY * this.CANVAS_HEIGHT;
        
        this.ctx.font = `bold ${this.state.textSize}px Arial`;
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = 'middle';
        
        // Draw background box
        const metrics = this.ctx.measureText(this.state.textContent);
        const padding = 20;
        const boxWidth = metrics.width + padding * 2;
        const boxHeight = this.state.textSize + padding * 2;
        
        this.ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
        this.ctx.fillRect(x - boxWidth/2, y - boxHeight/2, boxWidth, boxHeight);
        
        // Draw text
        this.ctx.fillStyle = this.state.textColor;
        this.ctx.fillText(this.state.textContent, x, y);
    }
    
    // Load image file
    loadImage(file, type) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (e) => {
                const img = new Image();
                img.onload = () => {
                    if (type === 'logo') {
                        this.state.logoFile = file;
                        this.state.logoImg = img;
                    } else if (type === 'watermark') {
                        this.state.watermarkFile = file;
                        this.state.watermarkImg = img;
                    }
                    this.render();
                    resolve(img);
                };
                img.onerror = reject;
                img.src = e.target.result;
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }
    
    // Update state from controls
    updateState(updates) {
        Object.assign(this.state, updates);
        this.render();
    }
    
    // Export state for API
    exportState() {
        return {
            logo_x: this.state.logoX,
            logo_y: this.state.logoY,
            logo_scale: this.state.logoScale,
            logo_opacity: this.state.logoOpacity,
            wm_mode: this.state.wmMode,
            wm_x: this.state.wmX,
            wm_y: this.state.wmY,
            wm_scale: this.state.wmScale,
            wm_opacity: this.state.wmOpacity,
            text_enabled: this.state.textEnabled,
            text_content: this.state.textContent,
            text_x_percent: this.state.textX,
            text_y_percent: this.state.textY,
            text_size: this.state.textSize,
            text_color: this.state.textColor
        };
    }
    
    // Load existing brand data
    loadState(brand) {
        this.state.logoX = brand.logo_x || 0.85;
        this.state.logoY = brand.logo_y || 0.85;
        this.state.logoScale = brand.logo_scale || 0.15;
        this.state.logoOpacity = brand.logo_opacity || 1.0;
        
        this.state.wmMode = brand.wm_mode || 'fullscreen';
        this.state.wmX = brand.wm_x || 0.5;
        this.state.wmY = brand.wm_y || 0.5;
        this.state.wmScale = brand.wm_scale || 1.0;
        this.state.wmOpacity = brand.wm_opacity || 0.10;
        
        this.state.textEnabled = brand.text_enabled || false;
        this.state.textContent = brand.text_content || '';
        this.state.textX = brand.text_x_percent || 0.5;
        this.state.textY = brand.text_y_percent || 0.2;
        this.state.textSize = brand.text_size || 48;
        this.state.textColor = brand.text_color || '#FFFFFF';
        
        // TODO: Load existing logo/watermark images if paths exist
        // This requires fetching from backend
        
        this.render();
    }
}

// Global instance
let brandEditor = null;

// Initialize editor when modal opens
function initBrandEditor() {
    if (!brandEditor) {
        brandEditor = new BrandEditor('brandPreviewCanvas');
    }
    return brandEditor;
}
