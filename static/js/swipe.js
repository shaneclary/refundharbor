// swipe.js — Touch gesture handling for mobile dashboard
//
// Handles swipeable cards, pull-to-refresh, and other touch interactions.

(function() {
    'use strict';

    // ── Swipeable Cards ───────────────────────────────────────────────────────
    class SwipeableCards {
        constructor(container, options = {}) {
            this.container = container;
            this.cards = container.querySelectorAll('.swipe-card');
            this.currentIndex = 0;
            this.startX = 0;
            this.startY = 0;
            this.isDragging = false;
            this.threshold = options.threshold || 50;
            this.onSwipe = options.onSwipe || (() => {});

            this.init();
        }

        init() {
            this.container.addEventListener('touchstart', this.handleTouchStart.bind(this), { passive: true });
            this.container.addEventListener('touchmove', this.handleTouchMove.bind(this), { passive: false });
            this.container.addEventListener('touchend', this.handleTouchEnd.bind(this), { passive: true });
        }

        handleTouchStart(e) {
            this.startX = e.touches[0].clientX;
            this.startY = e.touches[0].clientY;
            this.isDragging = true;
        }

        handleTouchMove(e) {
            if (!this.isDragging) return;

            const diffX = e.touches[0].clientX - this.startX;
            const diffY = e.touches[0].clientY - this.startY;

            // If horizontal swipe is greater than vertical, prevent scroll
            if (Math.abs(diffX) > Math.abs(diffY)) {
                e.preventDefault();
            }
        }

        handleTouchEnd(e) {
            if (!this.isDragging) return;
            this.isDragging = false;

            const endX = e.changedTouches[0].clientX;
            const diffX = endX - this.startX;

            if (Math.abs(diffX) > this.threshold) {
                if (diffX > 0 && this.currentIndex > 0) {
                    // Swipe right - go to previous
                    this.currentIndex--;
                    this.onSwipe(this.currentIndex, 'right');
                } else if (diffX < 0 && this.currentIndex < this.cards.length - 1) {
                    // Swipe left - go to next
                    this.currentIndex++;
                    this.onSwipe(this.currentIndex, 'left');
                }
            }

            this.scrollToCard(this.currentIndex);
            this.updateIndicators();
        }

        scrollToCard(index) {
            const card = this.cards[index];
            if (card) {
                card.scrollIntoView({
                    behavior: 'smooth',
                    block: 'nearest',
                    inline: 'center'
                });
            }
        }

        updateIndicators() {
            const indicators = document.getElementById('cardIndicators');
            if (!indicators) return;

            indicators.innerHTML = Array.from(this.cards).map((_, i) =>
                `<div class="w-2 h-2 rounded-full transition-colors ${i === this.currentIndex ? 'bg-blue-500' : 'bg-slate-600'}"></div>`
            ).join('');
        }

        goTo(index) {
            if (index >= 0 && index < this.cards.length) {
                this.currentIndex = index;
                this.scrollToCard(index);
                this.updateIndicators();
            }
        }
    }

    // ── Pull to Refresh ───────────────────────────────────────────────────────
    class PullToRefresh {
        constructor(options = {}) {
            this.container = options.container || document.getElementById('mainContent');
            this.indicator = options.indicator || document.getElementById('pullIndicator');
            this.onRefresh = options.onRefresh || (() => {});
            this.threshold = options.threshold || 140;
            this.maxPull = options.maxPull || 180;

            this.startY = 0;
            this.currentY = 0;
            this.isPulling = false;
            this.isRefreshing = false;

            this.init();
        }

        init() {
            this.container.addEventListener('touchstart', this.handleTouchStart.bind(this), { passive: true });
            this.container.addEventListener('touchmove', this.handleTouchMove.bind(this), { passive: false });
            this.container.addEventListener('touchend', this.handleTouchEnd.bind(this), { passive: true });
        }

        handleTouchStart(e) {
            if (this.isRefreshing) return;
            if (this.container.scrollTop > 0) return;

            this.startY = e.touches[0].clientY;
            this.isPulling = true;
        }

        handleTouchMove(e) {
            if (!this.isPulling || this.isRefreshing) return;
            if (this.container.scrollTop > 0) return;

            this.currentY = e.touches[0].clientY;
            const diff = this.currentY - this.startY;

            if (diff > 0) {
                e.preventDefault();
                const pull = Math.min(diff, this.maxPull);
                const progress = pull / this.threshold;

                // Move indicator down
                this.indicator.style.transform = `translateY(${pull - 50}px)`;

                // Rotate spinner based on pull progress
                const spinner = this.indicator.querySelector('.spinner');
                if (spinner && progress < 1) {
                    spinner.style.transform = `rotate(${progress * 360}deg)`;
                    spinner.style.animation = 'none';
                } else if (spinner) {
                    spinner.style.animation = '';
                }
            }
        }

        handleTouchEnd(e) {
            if (!this.isPulling || this.isRefreshing) return;
            this.isPulling = false;

            const diff = this.currentY - this.startY;

            if (diff > this.threshold) {
                this.triggerRefresh();
            } else {
                this.reset();
            }
        }

        async triggerRefresh() {
            this.isRefreshing = true;

            // Show loading state
            this.indicator.style.transform = 'translateY(30px)';

            try {
                await this.onRefresh();
            } finally {
                setTimeout(() => {
                    this.reset();
                    this.isRefreshing = false;
                }, 500);
            }
        }

        reset() {
            this.indicator.style.transform = 'translateY(-100%)';
            this.startY = 0;
            this.currentY = 0;
        }
    }

    // ── Touch Feedback ────────────────────────────────────────────────────────
    function addTouchFeedback() {
        document.querySelectorAll('.touch-btn, button').forEach(btn => {
            btn.addEventListener('touchstart', () => {
                btn.style.transform = 'scale(0.97)';
            }, { passive: true });

            btn.addEventListener('touchend', () => {
                btn.style.transform = '';
            }, { passive: true });

            btn.addEventListener('touchcancel', () => {
                btn.style.transform = '';
            }, { passive: true });
        });
    }

    // ── Haptic Feedback (if available) ────────────────────────────────────────
    function haptic(style = 'light') {
        if ('vibrate' in navigator) {
            const patterns = {
                light: 10,
                medium: 20,
                heavy: 30
            };
            navigator.vibrate(patterns[style] || 10);
        }
    }

    // ── Initialize ────────────────────────────────────────────────────────────
    function init() {
        // Initialize swipeable cards
        const accountCards = document.getElementById('accountCards');
        if (accountCards) {
            window.accountSwiper = new SwipeableCards(accountCards, {
                onSwipe: (index, direction) => {
                    haptic('light');
                    // Could dispatch custom event here for account switching
                    const event = new CustomEvent('accountSwipe', { detail: { index, direction } });
                    document.dispatchEvent(event);
                }
            });
        }

        // Initialize pull to refresh
        if (document.getElementById('mainContent')) {
            window.pullRefresh = new PullToRefresh({
                onRefresh: async () => {
                    haptic('medium');
                    // Trigger app refresh
                    if (window.refreshAll) {
                        await window.refreshAll();
                    }
                }
            });
        }

        // Add touch feedback to buttons
        addTouchFeedback();
    }

    // Export for use in app.js
    window.SwipeableCards = SwipeableCards;
    window.PullToRefresh = PullToRefresh;
    window.haptic = haptic;

    // Auto-init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
