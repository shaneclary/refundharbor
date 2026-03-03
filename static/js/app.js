// app.js — DenseWealth Mobile Dashboard
//
// Main application logic for the mobile-first dashboard.
// Supports role-based UI (operator vs viewer) with request/approval workflow.

(function() {
    'use strict';

    // ── State ─────────────────────────────────────────────────────────────────
    const state = {
        user: null,  // { username, role }
        isOperator: false,
        accounts: [],
        activeAccountId: 1,
        activeTab: 'dashboard',
        mode: 'paper',
        approvalMode: 'auto',
        wallets: [],
        positions: [],
        trades: [],
        pendingTrades: [],
        pendingRequests: [],
        myRequests: [],
        isRefreshing: false,
        // Pending changes (for viewers to submit)
        pendingChanges: {},
        // Activation state
        activationData: null,
        selectedActivationMode: 'auto',
        // Futures state
        futuresStats: {},
        futuresPositions: [],
        futuresTrades: [],
        futuresWallets: [],
    };

    // ── API Helpers ───────────────────────────────────────────────────────────
    async function api(endpoint, options = {}) {
        const response = await fetch(`/api${endpoint}`, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
            body: options.body ? JSON.stringify(options.body) : undefined,
        });
        return response.json();
    }

    // ── User & Role ───────────────────────────────────────────────────────────
    async function loadUser() {
        const data = await api('/me');
        if (data.username) {
            state.user = data;
            state.isOperator = data.role === 'operator';
            updateUIForRole();
        }
    }

    function updateUIForRole() {
        const isOp = state.isOperator;

        // Show/hide admin-only elements
        document.querySelectorAll('.admin-only').forEach(el => {
            el.classList.toggle('hidden', !isOp);
        });

        // Show/hide viewer-only elements
        document.getElementById('submitReviewSection')?.classList.toggle('hidden', isOp);
        document.getElementById('myRequestsSection')?.classList.toggle('hidden', isOp);
        document.getElementById('adminAccountSection')?.classList.toggle('hidden', !isOp);

        // Update tab indicator width based on visible tabs
        updateTabIndicatorWidth();

        // Load role-specific data
        if (isOp) {
            loadAdminData();
        } else {
            loadMyRequests();
        }
    }

    function updateTabIndicatorWidth() {
        const visibleTabs = document.querySelectorAll('.tab-btn:not(.hidden)');
        const indicator = document.getElementById('tabIndicator');
        if (indicator && visibleTabs.length > 0) {
            indicator.style.width = `${100 / visibleTabs.length}%`;
        }
    }

    // ── Account Activation ───────────────────────────────────────────────────

    async function checkActivationPrompt() {
        // Only check for operators
        if (!state.isOperator) return;

        try {
            const data = await api('/activation/prompt');
            if (data.show_prompt) {
                showActivationModal({
                    account_name: data.account_name,
                    balance: data.balance,
                });
            }
        } catch (err) {
            console.error('Activation check failed:', err);
        }
    }

    function showActivationModal(data) {
        const modal = document.getElementById('activationModal');
        if (!modal) return;

        // Populate account info
        document.getElementById('activationAccountName').textContent = data.account_name || 'Main';
        document.getElementById('activationBalance').textContent = `$${(data.balance || 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}`;

        // Default to global mode (auto-configured)
        state.selectedActivationMode = 'global';

        // Show modal
        modal.classList.remove('hidden');
    }

    function hideActivationModal() {
        const modal = document.getElementById('activationModal');
        if (modal) {
            modal.classList.add('hidden');
        }
    }

    async function handleActivationApprove() {
        const btn = document.getElementById('activationApproveBtn');
        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Activating...';

        try {
            const result = await api('/activation/activate', {
                method: 'POST',
                body: {
                    mode: state.selectedActivationMode,
                    confirm: true,
                },
            });

            if (result.success) {
                hideActivationModal();
                showToast(result.message || 'Account activated!');
                // Refresh to show new mode
                await refreshAll();
            } else {
                showToast(result.error || 'Activation failed', 'error');
            }
        } catch (err) {
            showToast('Activation failed: ' + err.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    }

    async function handleActivationDeny() {
        hideActivationModal();
        showToast('Staying in paper trading mode');
    }

    async function updateTradingStatus() {
        // Only relevant for operators
        if (!state.isOperator) return;

        const statusCard = document.getElementById('tradingStatusCard');
        const statusDot = document.getElementById('tradingStatusDot');
        const statusText = document.getElementById('tradingStatusText');
        const statusDesc = document.getElementById('tradingStatusDesc');
        const goLiveBtn = document.getElementById('goLiveBtn');

        if (!statusCard) return;

        try {
            const data = await api('/activation/status');
            const isLive = data.is_activated && state.mode !== 'paper';

            if (isLive) {
                // Live mode styling
                statusCard.className = 'bg-gradient-to-r from-emerald-600/20 to-blue-600/20 border border-emerald-500/30 rounded-xl p-4 mb-4';
                statusDot.className = 'w-3 h-3 rounded-full bg-emerald-500 animate-pulse';
                statusText.textContent = `Live - ${state.mode.toUpperCase()}`;
                statusDesc.textContent = 'Real orders are being executed. Auto-trading is active.';
                goLiveBtn.textContent = 'Deactivate';
                goLiveBtn.className = 'touch-btn px-4 py-2 bg-red-600/20 hover:bg-red-600/30 text-red-400 border border-red-500/30 rounded-lg font-medium text-sm transition-colors';
            } else {
                // Paper mode styling
                statusCard.className = 'bg-gradient-to-r from-amber-600/20 to-orange-600/20 border border-amber-500/30 rounded-xl p-4 mb-4';
                statusDot.className = 'w-3 h-3 rounded-full bg-amber-500 animate-pulse';
                statusText.textContent = 'Paper Mode';
                statusDesc.textContent = data.can_activate
                    ? 'Trades are simulated. Click "Go Live" to activate real trading.'
                    : 'Trades are simulated. Add API credentials to enable live trading.';
                goLiveBtn.textContent = 'Go Live';
                goLiveBtn.className = 'touch-btn px-4 py-2 bg-gradient-to-r from-emerald-600 to-blue-600 hover:from-emerald-500 hover:to-blue-500 rounded-lg font-medium text-sm transition-colors shadow-lg';
                goLiveBtn.disabled = !data.can_activate;

                if (!data.can_activate) {
                    goLiveBtn.className = 'touch-btn px-4 py-2 bg-slate-700 text-slate-500 rounded-lg font-medium text-sm cursor-not-allowed';
                }
            }
        } catch (err) {
            console.error('Failed to update trading status:', err);
        }
    }

    async function handleGoLiveClick() {
        // Check current status
        const data = await api('/activation/status');

        if (data.is_activated && state.mode !== 'paper') {
            // Currently live - deactivate
            if (confirm('This will switch to Paper mode and disable auto-trading. Continue?')) {
                const result = await api('/activation/deactivate', { method: 'POST' });
                if (result.success) {
                    showToast(result.message);
                    await refreshAll();
                    await updateTradingStatus();
                }
            }
        } else {
            // Currently paper - show activation modal
            if (data.can_activate) {
                showActivationModal({
                    account_name: data.account_name,
                    balance: data.balance,
                });
            } else {
                showToast('Add API credentials before going live', 'error');
            }
        }
    }

    // ── Environment Variables (Vercel-style) ───────────────────────────────

    const ENV_VAR_CONFIG = {
        'POLY_API_KEY': { label: 'API Key', type: 'api_key', description: 'Your Polymarket API key from the developer portal' },
        'POLY_API_SECRET': { label: 'API Secret', type: 'api_secret', description: 'Your Polymarket API secret (keep this private)' },
        'POLY_API_PASSPHRASE': { label: 'API Passphrase', type: 'api_passphrase', description: 'The passphrase you set when creating API keys' },
        'POLY_PRIVATE_KEY': { label: 'Private Key', type: 'private_key', description: 'Your wallet private key (0x...) for signing transactions' },
    };

    async function loadEnvVars() {
        if (!state.isOperator) return;

        const container = document.getElementById('envVarsList');
        const warningBanner = document.getElementById('rotationWarning');
        if (!container) return;

        try {
            const data = await api(`/accounts/${state.activeAccountId}/credentials`);

            if (data.error) {
                container.innerHTML = `
                    <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-center">
                        <p class="text-sm text-red-400">${data.error}</p>
                        <p class="text-xs text-slate-500 mt-1">Set DENSEWEALTH_MASTER_KEY on server</p>
                    </div>
                `;
                return;
            }

            const creds = data.credentials || [];
            const needsRotation = data.needs_rotation || [];

            // Show rotation warning if needed
            if (needsRotation.length > 0 && warningBanner) {
                warningBanner.classList.remove('hidden');
                document.getElementById('rotationWarningText').textContent =
                    `${needsRotation.length} key(s) over 90 days old: ${needsRotation.join(', ')}`;
            } else if (warningBanner) {
                warningBanner.classList.add('hidden');
            }

            // Build list of all possible vars
            const allVars = Object.entries(ENV_VAR_CONFIG).map(([key, config]) => {
                const existing = creds.find(c => c.credential_type === config.type);
                return {
                    key,
                    ...config,
                    configured: !!existing,
                    age_days: existing?.age_days || 0,
                    needs_rotation: existing?.needs_rotation || false,
                    rotation_warning: existing?.rotation_warning || false,
                };
            });

            if (allVars.length === 0) {
                container.innerHTML = '<div class="text-center text-slate-500 py-4 text-sm">No variables configured</div>';
                return;
            }

            container.innerHTML = allVars.map(v => `
                <div class="flex items-center justify-between bg-navy-800/50 rounded-lg p-3 group">
                    <div class="flex items-center gap-3 flex-1 min-w-0">
                        <div class="w-2 h-2 rounded-full flex-shrink-0 ${v.configured ? (v.needs_rotation ? 'bg-amber-500' : 'bg-emerald-500') : 'bg-slate-600'}"></div>
                        <div class="min-w-0">
                            <div class="font-mono text-sm truncate">${v.key}</div>
                            <div class="text-xs text-slate-500">
                                ${v.configured
                                    ? (v.needs_rotation
                                        ? `<span class="text-amber-400">⚠ ${v.age_days} days old - rotate now</span>`
                                        : (v.rotation_warning
                                            ? `<span class="text-amber-400">${v.age_days} days old</span>`
                                            : `${v.age_days} days old`))
                                    : 'Not configured'}
                            </div>
                        </div>
                    </div>
                    <div class="flex items-center gap-2">
                        ${v.configured ? `
                            <button onclick="editEnvVar('${v.key}')" class="touch-btn p-2 rounded-lg hover:bg-slate-700/50 text-slate-400 hover:text-white transition-colors">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                                </svg>
                            </button>
                            <button onclick="deleteEnvVar('${v.key}')" class="touch-btn p-2 rounded-lg hover:bg-red-500/20 text-slate-400 hover:text-red-400 transition-colors">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                                </svg>
                            </button>
                        ` : `
                            <button onclick="addEnvVar('${v.key}')" class="touch-btn px-3 py-1 rounded-lg bg-blue-600 hover:bg-blue-500 text-sm font-medium transition-colors">
                                Add
                            </button>
                        `}
                    </div>
                </div>
            `).join('');

        } catch (err) {
            console.error('Failed to load env vars:', err);
            container.innerHTML = '<div class="text-center text-red-400 py-4 text-sm">Failed to load</div>';
        }
    }

    // Make functions globally accessible for onclick handlers
    window.addEnvVar = function(key) {
        showEnvVarModal(key, false);
    };

    window.editEnvVar = function(key) {
        showEnvVarModal(key, true);
    };

    window.deleteEnvVar = async function(key) {
        const config = ENV_VAR_CONFIG[key];
        if (!config) return;

        if (!confirm(`Delete ${key}? This cannot be undone.`)) return;

        try {
            const result = await api(`/accounts/${state.activeAccountId}/credentials/polymarket_global/${config.type}`, {
                method: 'DELETE',
            });

            if (result.deleted) {
                showToast(`${key} deleted`);
                await loadEnvVars();
                await updateTradingStatus();
            } else {
                showToast('Failed to delete', 'error');
            }
        } catch (err) {
            showToast('Delete failed: ' + err.message, 'error');
        }
    };

    function showEnvVarModal(key, isEdit) {
        const modal = document.getElementById('envVarModal');
        if (!modal) return;

        const config = ENV_VAR_CONFIG[key];
        const titleEl = document.getElementById('envVarModalTitle');
        const keySelect = document.getElementById('envVarKey');
        const valueInput = document.getElementById('envVarValue');
        const descEl = document.getElementById('envVarDescription');
        const descText = document.getElementById('envVarDescText');

        titleEl.textContent = isEdit ? 'Update Environment Variable' : 'Add Environment Variable';
        keySelect.value = key || '';
        valueInput.value = '';
        valueInput.type = 'password';

        if (key && config) {
            descEl.classList.remove('hidden');
            descText.textContent = config.description;
        } else {
            descEl.classList.add('hidden');
        }

        modal.classList.remove('hidden');
    }

    function hideEnvVarModal() {
        const modal = document.getElementById('envVarModal');
        if (modal) {
            modal.classList.add('hidden');
        }
    }

    async function saveEnvVar() {
        const keySelect = document.getElementById('envVarKey');
        const valueInput = document.getElementById('envVarValue');
        const btn = document.getElementById('saveEnvVarBtn');

        const key = keySelect.value;
        const value = valueInput.value.trim();

        if (!key) {
            showToast('Select a variable', 'error');
            return;
        }

        if (!value) {
            showToast('Enter a value', 'error');
            return;
        }

        const config = ENV_VAR_CONFIG[key];
        if (!config) return;

        btn.disabled = true;
        btn.textContent = 'Saving...';

        try {
            const result = await api(`/accounts/${state.activeAccountId}/credentials`, {
                method: 'POST',
                body: {
                    platform: 'polymarket_global',
                    credential_type: config.type,
                    value: value,
                },
            });

            if (result.success) {
                hideEnvVarModal();
                showToast(`${key} saved`);
                await loadEnvVars();
                await updateTradingStatus();
            } else {
                showToast(result.error || 'Save failed', 'error');
            }
        } catch (err) {
            showToast('Save failed: ' + err.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = 'Save';
        }
    }

    function toggleEnvVarVisibility() {
        const input = document.getElementById('envVarValue');
        input.type = input.type === 'password' ? 'text' : 'password';
    }

    // ── Data Loading ──────────────────────────────────────────────────────────
    async function loadAccounts() {
        const data = await api('/accounts');
        if (data.accounts) {
            state.accounts = data.accounts;
            state.activeAccountId = data.active_account_id;
            renderAccountCards();
        }
    }

    async function loadStats() {
        const stats = await api('/stats');
        if (stats.balance !== undefined) {
            document.getElementById('accountBalance').textContent = `$${stats.balance.toFixed(2)}`;
            const pnl = stats.total_pnl || 0;
            const pnlEl = document.getElementById('accountPnl');
            pnlEl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
            pnlEl.className = pnl >= 0 ? 'text-lg font-semibold text-emerald-400' : 'text-lg font-semibold text-red-400';
            document.getElementById('accountTrades').textContent = stats.total_trades || 0;

            const pnlPct = stats.pnl_pct || 0;
            document.getElementById('returnPct').textContent = `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%`;
            document.getElementById('returnPct').className = `text-xl font-semibold ${pnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`;
        }

        // Also load mode
        const modeData = await api('/mode');
        if (modeData.mode) {
            state.mode = modeData.mode;
        }
    }

    async function loadPnl() {
        const pnl = await api('/pnl?period=all');
        if (pnl.win_rate !== undefined) {
            document.getElementById('winRate').textContent = `${pnl.win_rate.toFixed(0)}%`;
            document.getElementById('winRateBar').style.width = `${Math.min(pnl.win_rate, 100)}%`;
        }
    }

    async function loadWallets() {
        const wallets = await api('/wallets');
        if (Array.isArray(wallets)) {
            state.wallets = wallets;
            renderWallets();
        }
    }

    async function loadPositions() {
        const positions = await api('/positions');
        if (Array.isArray(positions)) {
            state.positions = positions;
            renderPositions();
        }
    }

    async function loadTrades() {
        const trades = await api('/trades');
        if (Array.isArray(trades)) {
            state.trades = trades;
            renderRecentTrades();
        }
    }

    async function loadPendingTrades() {
        const data = await api('/pending-trades');
        if (data.trades) {
            state.pendingTrades = data.trades;
            renderPendingTrades();
        }
    }

    async function loadMode() {
        const data = await api('/mode');
        if (data.mode) {
            state.mode = data.mode;
            updateModeIndicator();
            updateModeButtons();
        }
    }

    async function loadApprovalMode() {
        const data = await api('/approval-mode');
        if (data.mode) {
            state.approvalMode = data.mode;
            updateApprovalToggle();
        }
    }

    async function loadReserve() {
        const reserve = await api(`/accounts/${state.activeAccountId}/reserve`);
        if (reserve.reserve_pct !== undefined) {
            document.getElementById('reserveSlider').value = reserve.reserve_pct;
            document.getElementById('reservePctValue').textContent = `${reserve.reserve_pct}%`;
            document.getElementById('accountReserve').textContent = `$${reserve.reserve_balance.toFixed(2)}`;

            const cyclingToggle = document.getElementById('cyclingToggle');
            if (reserve.cycling_enabled) {
                cyclingToggle.classList.add('active');
                document.getElementById('cyclingOptions').classList.remove('hidden');
            } else {
                cyclingToggle.classList.remove('active');
                document.getElementById('cyclingOptions').classList.add('hidden');
            }

            document.getElementById('cycleSlider').value = reserve.cycle_pct || 10;
            document.getElementById('cyclePctValue').textContent = `${reserve.cycle_pct || 10}%`;

            // Update schedule buttons
            document.querySelectorAll('.schedule-btn').forEach(btn => {
                if (btn.dataset.schedule === reserve.cycle_schedule) {
                    btn.classList.add('bg-blue-600');
                    btn.classList.remove('bg-navy-600');
                } else {
                    btn.classList.remove('bg-blue-600');
                    btn.classList.add('bg-navy-600');
                }
            });
        }
    }

    async function loadProfile() {
        const profile = await api(`/accounts/${state.activeAccountId}/profile`);
        if (profile.risk_level) {
            updateRiskButtons(profile.risk_level);
            document.getElementById('maxTradePct').textContent = `${(profile.max_trade_pct * 100).toFixed(0)}%`;
            document.getElementById('maxWalletPct').textContent = `${(profile.max_wallet_pct * 100).toFixed(0)}%`;
            document.getElementById('maxMarketPct').textContent = `${(profile.max_market_pct * 100).toFixed(0)}%`;
        }
    }

    async function loadFunds() {
        const [allocData, fundsData] = await Promise.all([
            api('/allocations'),
            api('/funds'),
        ]);

        const section = document.getElementById('fundsSection');
        const container = document.getElementById('fundCards');
        if (!section || !container) return;

        const funds = (allocData && allocData.funds) ? allocData.funds : [];
        const fundDetails = Array.isArray(fundsData) ? fundsData : [];

        // Only show if there's any allocation activity or main balance > 0
        const hasActivity = funds.some(f => f.total_allocated > 0);
        const mainFund = fundDetails.find(f => f.fund_id === 'main');
        const mainBalance = mainFund ? mainFund.balance : 0;

        if (!hasActivity && mainBalance < 1000) {
            section.classList.add('hidden');
            return;
        }
        section.classList.remove('hidden');

        const colorMap = { Charity: 'emerald', Savings: 'yellow', Family: 'purple' };
        const iconMap = { Charity: '&#9829;', Savings: '&#128179;', Family: '&#128106;' };

        let html = '';

        // Summary bar
        const totalAllocated = funds.reduce((sum, f) => sum + f.total_allocated, 0);
        const tradingPct = allocData.trading_pct || 80;
        html += `<div class="flex items-center justify-between px-3 py-2 rounded-lg bg-slate-800/50 border border-slate-700/50 mb-3">
            <span class="text-xs text-slate-500">Total set aside</span>
            <span class="text-sm font-semibold text-white">$${totalAllocated.toFixed(2)}</span>
        </div>`;

        // Fund cards
        for (const fund of funds) {
            const color = colorMap[fund.name] || 'blue';
            const icon = iconMap[fund.name] || '&#128176;';
            const detail = fundDetails.find(f => f.name === fund.name) || {};
            const status = detail.status || 'active';
            const walletShort = fund.wallet ? fund.wallet.slice(0, 6) + '...' + fund.wallet.slice(-4) : 'Not set';

            const statusBadge = status === 'waiting'
                ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400">Waiting</span>'
                : '<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400">Active</span>';

            html += `
            <div class="p-3 rounded-xl bg-slate-800/60 border border-slate-700/50">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-2">
                        <span class="text-base">${icon}</span>
                        <span class="text-sm font-medium text-white">${fund.name}</span>
                        <span class="text-xs text-slate-500">${fund.pct}%</span>
                    </div>
                    ${statusBadge}
                </div>
                <div class="grid grid-cols-3 gap-3 text-center">
                    <div>
                        <div class="text-xs text-slate-500">Allocated</div>
                        <div class="text-sm font-semibold text-${color}-400">$${fund.total_allocated.toFixed(2)}</div>
                    </div>
                    <div>
                        <div class="text-xs text-slate-500">Pending</div>
                        <div class="text-sm text-slate-300">$${fund.pending.toFixed(2)}</div>
                    </div>
                    <div>
                        <div class="text-xs text-slate-500">Transferred</div>
                        <div class="text-sm text-slate-300">$${fund.transferred.toFixed(2)}</div>
                    </div>
                </div>
                <div class="mt-2 flex items-center justify-between text-[10px] text-slate-600">
                    <span>Wallet: ${walletShort}</span>
                    <span>${fund.num_allocations} allocation${fund.num_allocations !== 1 ? 's' : ''}</span>
                </div>
            </div>`;
        }

        // Trading retention note
        html += `<div class="text-center text-[10px] text-slate-600 mt-2">${tradingPct}% of profits stay in trading balance</div>`;

        container.innerHTML = html;
    }

    async function loadMyRequests() {
        const data = await api('/settings-requests?my_requests=true');
        if (data.requests) {
            state.myRequests = data.requests;
            renderMyRequests();
        }
    }

    async function loadAdminData() {
        const data = await api('/admin/overview');
        if (data.pending_trades) {
            document.getElementById('adminPendingTrades').textContent = data.pending_trades.count;
            document.getElementById('adminPendingRequests').textContent = data.pending_requests.count;

            // Show badge if there are pending items
            const badge = document.getElementById('adminBadge');
            if (badge) {
                badge.classList.toggle('hidden', data.pending_requests.count === 0);
            }

            state.pendingRequests = data.pending_requests.requests;
            renderAdminRequests();
        }

        // Load activity
        const activity = await api('/admin/activity?limit=20');
        if (Array.isArray(activity)) {
            renderAdminActivity(activity);
        }
    }

    // ── Futures Data Loading ──────────────────────────────────────────────────
    async function loadFuturesStats() {
        const stats = await api('/futures/stats');
        if (stats.balance !== undefined) {
            state.futuresStats = stats;
            renderFuturesStats();
        }
    }

    async function loadFuturesPositions() {
        const positions = await api('/futures/positions');
        if (Array.isArray(positions)) {
            state.futuresPositions = positions;
            renderFuturesPositions();
        }
    }

    async function loadFuturesTrades() {
        const trades = await api('/futures/trades?limit=20');
        if (Array.isArray(trades)) {
            state.futuresTrades = trades;
            renderFuturesTrades();
        }
    }

    async function loadFuturesWallets() {
        const wallets = await api('/futures/wallets');
        if (Array.isArray(wallets)) {
            state.futuresWallets = wallets;
            renderFuturesWallets();
        }
    }

    async function loadFuturesData() {
        await Promise.all([
            loadFuturesStats(),
            loadFuturesPositions(),
            loadFuturesTrades(),
            loadFuturesWallets(),
        ]);
    }

    async function refreshAll() {
        if (state.isRefreshing) return;
        state.isRefreshing = true;

        const pullIndicator = document.getElementById('pullIndicator');
        pullIndicator.style.transform = 'translateY(0)';

        try {
            await Promise.all([
                loadStats(),
                loadPnl(),
                loadWallets(),
                loadPositions(),
                loadTrades(),
                loadPendingTrades(),
                loadMode(),
                loadApprovalMode(),
                loadReserve(),
                loadProfile(),
                loadFunds(),
                loadFuturesData(),
            ]);

            if (state.isOperator) {
                await loadAdminData();
                await updateTradingStatus();
                await loadEnvVars();
            } else {
                await loadMyRequests();
            }
        } catch (err) {
            console.error('Refresh error:', err);
        }

        setTimeout(() => {
            pullIndicator.style.transform = 'translateY(-100%)';
            state.isRefreshing = false;
        }, 500);
    }

    // ── Rendering ─────────────────────────────────────────────────────────────
    function renderAccountCards() {
        if (state.accounts.length === 0) return;

        const active = state.accounts.find(a => a.id === state.activeAccountId) || state.accounts[0];
        if (active) {
            document.getElementById('accountBalance').textContent = `$${(active.balance_usdc || 0).toFixed(2)}`;
            const pnl = active.total_pnl || 0;
            const pnlEl = document.getElementById('accountPnl');
            pnlEl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
            pnlEl.className = pnl >= 0 ? 'text-lg font-semibold text-emerald-400' : 'text-lg font-semibold text-red-400';
            document.getElementById('accountReserve').textContent = `$${(active.reserve_balance || 0).toFixed(2)}`;
            document.getElementById('accountTrades').textContent = active.total_trades || 0;
        }

        renderAccountsList();
    }

    function renderAccountsList() {
        const container = document.getElementById('accountsList');
        if (!container) return;

        if (state.accounts.length === 0) {
            container.innerHTML = '<div class="text-center text-slate-500 py-8">No accounts configured</div>';
            return;
        }

        container.innerHTML = state.accounts.map(a => {
            const isActive = a.id === state.activeAccountId;
            const pnl = a.total_pnl || 0;
            const pnlColor = pnl >= 0 ? 'text-emerald-400' : 'text-red-400';
            const pnlSign = pnl >= 0 ? '+' : '';
            const statusColor = a.status === 'active' ? 'bg-emerald-500' : 'bg-slate-500';
            const riskColors = { conservative: 'text-blue-400', moderate: 'text-amber-400', aggressive: 'text-red-400' };
            const riskColor = riskColors[a.profile?.risk_level] || 'text-slate-400';

            return `
            <div class="bg-navy-700/50 rounded-xl p-4 ${isActive ? 'ring-1 ring-blue-500/50' : ''}" data-account-id="${a.id}">
                <div class="flex items-center justify-between mb-3">
                    <div class="flex items-center gap-3">
                        <div class="w-2 h-2 rounded-full ${statusColor}"></div>
                        <div>
                            <div class="font-medium flex items-center gap-2">
                                ${a.name}
                                ${isActive ? '<span class="text-xs bg-blue-600/30 text-blue-400 px-2 py-0.5 rounded-full">Active</span>' : ''}
                            </div>
                            <div class="text-xs text-slate-400">${a.account_type} &middot; <span class="${riskColor}">${a.profile?.risk_level || 'moderate'}</span></div>
                        </div>
                    </div>
                    <button class="account-detail-btn touch-btn p-2 text-slate-400 hover:text-white" data-account-id="${a.id}">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                        </svg>
                    </button>
                </div>
                <div class="grid grid-cols-3 gap-3 text-center">
                    <div class="bg-navy-600/50 rounded-lg p-2">
                        <div class="text-xs text-slate-400">Balance</div>
                        <div class="text-sm font-semibold">$${(a.balance_usdc || 0).toFixed(2)}</div>
                    </div>
                    <div class="bg-navy-600/50 rounded-lg p-2">
                        <div class="text-xs text-slate-400">P&L</div>
                        <div class="text-sm font-semibold ${pnlColor}">${pnlSign}$${Math.abs(pnl).toFixed(2)}</div>
                    </div>
                    <div class="bg-navy-600/50 rounded-lg p-2">
                        <div class="text-xs text-slate-400">Trades</div>
                        <div class="text-sm font-semibold">${a.total_trades || 0}</div>
                    </div>
                </div>
                ${!isActive ? `
                <button class="switch-account-btn touch-btn w-full mt-3 py-2 bg-navy-600 hover:bg-navy-500 rounded-lg text-sm font-medium transition-colors" data-account-id="${a.id}">
                    Switch to This Account
                </button>` : ''}
            </div>`;
        }).join('');

        // Bind click handlers
        container.querySelectorAll('.switch-account-btn').forEach(btn => {
            btn.addEventListener('click', () => handleSwitchAccount(parseInt(btn.dataset.accountId)));
        });
        container.querySelectorAll('.account-detail-btn').forEach(btn => {
            btn.addEventListener('click', () => showAccountDetail(parseInt(btn.dataset.accountId)));
        });
    }

    async function handleSwitchAccount(accountId) {
        const result = await api(`/accounts/${accountId}/activate`, { method: 'POST' });
        if (result.success) {
            state.activeAccountId = accountId;
            showToast(`Switched to ${result.account?.name || 'account'}`);
            await loadAccounts();
            await refreshAll();
        } else {
            showToast(result.error || 'Failed to switch', 'error');
        }
    }

    function showAccountDetail(accountId) {
        const account = state.accounts.find(a => a.id === accountId);
        if (!account) return;

        const modal = document.getElementById('accountDetailModal');
        const content = document.getElementById('accountDetailContent');

        const pnl = account.total_pnl || 0;
        const pnlColor = pnl >= 0 ? 'text-emerald-400' : 'text-red-400';
        const isMain = account.id === 1;

        content.innerHTML = `
            <div class="flex items-center justify-between mb-6">
                <h2 class="text-lg font-semibold text-white">${account.name}</h2>
                <button class="close-detail-btn touch-btn p-2 text-slate-400 hover:text-white">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            </div>

            <div class="space-y-4">
                ${account.description ? `<p class="text-sm text-slate-400">${account.description}</p>` : ''}

                <!-- Balance Overview -->
                <div class="bg-navy-700/50 rounded-xl p-4">
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <div class="text-xs text-slate-400">Balance</div>
                            <div class="text-lg font-semibold">$${(account.balance_usdc || 0).toFixed(2)}</div>
                        </div>
                        <div>
                            <div class="text-xs text-slate-400">P&L</div>
                            <div class="text-lg font-semibold ${pnlColor}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</div>
                        </div>
                        <div>
                            <div class="text-xs text-slate-400">Reserve</div>
                            <div class="text-lg font-semibold">$${(account.reserve_balance || 0).toFixed(2)}</div>
                        </div>
                        <div>
                            <div class="text-xs text-slate-400">Tradable</div>
                            <div class="text-lg font-semibold">$${(account.tradable_balance || 0).toFixed(2)}</div>
                        </div>
                    </div>
                </div>

                <!-- Trading Profile -->
                <div class="bg-navy-700/50 rounded-xl p-4">
                    <h3 class="text-sm font-medium text-slate-300 mb-3">Trading Profile</h3>
                    <div class="space-y-2 text-sm">
                        <div class="flex justify-between"><span class="text-slate-400">Risk Level</span><span class="capitalize">${account.profile?.risk_level || 'moderate'}</span></div>
                        <div class="flex justify-between"><span class="text-slate-400">Strategy</span><span>${account.profile?.copy_strategy || 'tiered_fixed'}</span></div>
                        <div class="flex justify-between"><span class="text-slate-400">Auto-Trade</span><span>${account.profile?.auto_trade_enabled ? 'Enabled' : 'Disabled'}</span></div>
                        <div class="flex justify-between"><span class="text-slate-400">Max Trade</span><span>${((account.profile?.max_trade_pct || 0) * 100).toFixed(0)}%</span></div>
                        <div class="flex justify-between"><span class="text-slate-400">Max Wallet</span><span>${((account.profile?.max_wallet_pct || 0) * 100).toFixed(0)}%</span></div>
                    </div>
                </div>

                <!-- Fund Actions -->
                <div class="bg-navy-700/50 rounded-xl p-4 admin-only">
                    <h3 class="text-sm font-medium text-slate-300 mb-3">Manage Funds</h3>
                    <div class="flex gap-2">
                        <input id="fundAmount-${account.id}" type="number" step="0.01" min="0" placeholder="Amount" class="flex-1 bg-navy-600 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500">
                        <button class="add-funds-btn touch-btn px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium transition-colors" data-account-id="${account.id}">Add</button>
                        <button class="withdraw-funds-btn touch-btn px-4 py-2 bg-amber-600 hover:bg-amber-500 rounded-lg text-sm font-medium transition-colors" data-account-id="${account.id}">Withdraw</button>
                    </div>
                </div>

                <!-- Danger Zone -->
                ${!isMain ? `
                <div class="bg-red-900/20 border border-red-800/30 rounded-xl p-4 admin-only">
                    <h3 class="text-sm font-medium text-red-400 mb-3">Danger Zone</h3>
                    <button class="delete-account-btn touch-btn w-full py-2 bg-red-600/20 hover:bg-red-600/30 text-red-400 rounded-lg text-sm font-medium transition-colors" data-account-id="${account.id}">
                        Delete Account
                    </button>
                </div>` : ''}
            </div>
        `;

        // Bind handlers
        content.querySelector('.close-detail-btn').addEventListener('click', () => {
            modal.classList.add('hidden');
        });

        content.querySelectorAll('.add-funds-btn').forEach(btn => {
            btn.addEventListener('click', () => handleAddFunds(parseInt(btn.dataset.accountId)));
        });
        content.querySelectorAll('.withdraw-funds-btn').forEach(btn => {
            btn.addEventListener('click', () => handleWithdrawFunds(parseInt(btn.dataset.accountId)));
        });
        content.querySelectorAll('.delete-account-btn').forEach(btn => {
            btn.addEventListener('click', () => handleDeleteAccount(parseInt(btn.dataset.accountId)));
        });

        // Show/hide admin elements based on role
        if (!state.isOperator) {
            content.querySelectorAll('.admin-only').forEach(el => el.classList.add('hidden'));
        }

        modal.classList.remove('hidden');
    }

    async function handleAddFunds(accountId) {
        const input = document.getElementById(`fundAmount-${accountId}`);
        const amount = parseFloat(input.value);
        if (!amount || amount <= 0) { showToast('Enter a valid amount', 'error'); return; }

        const result = await api(`/accounts/${accountId}/funds/add`, {
            method: 'POST',
            body: { amount }
        });
        if (result.balance_usdc !== undefined) {
            showToast(`Added $${amount.toFixed(2)}`);
            input.value = '';
            await loadAccounts();
            showAccountDetail(accountId);
        } else {
            showToast(result.error || 'Failed', 'error');
        }
    }

    async function handleWithdrawFunds(accountId) {
        const input = document.getElementById(`fundAmount-${accountId}`);
        const amount = parseFloat(input.value);
        if (!amount || amount <= 0) { showToast('Enter a valid amount', 'error'); return; }

        const result = await api(`/accounts/${accountId}/funds/withdraw`, {
            method: 'POST',
            body: { amount }
        });
        if (result.balance_usdc !== undefined) {
            showToast(`Withdrew $${amount.toFixed(2)}`);
            input.value = '';
            await loadAccounts();
            showAccountDetail(accountId);
        } else {
            showToast(result.detail || result.error || 'Insufficient balance', 'error');
        }
    }

    async function handleDeleteAccount(accountId) {
        if (!confirm('Delete this account? This cannot be undone.')) return;

        const result = await api(`/accounts/${accountId}`, { method: 'DELETE' });
        if (result.success) {
            document.getElementById('accountDetailModal').classList.add('hidden');
            showToast('Account deleted');
            await loadAccounts();
        } else {
            showToast(result.detail || result.error || 'Cannot delete', 'error');
        }
    }

    async function handleCreateAccount() {
        const name = document.getElementById('newAccountName').value.trim();
        if (!name) { showToast('Enter a name', 'error'); return; }

        const desc = document.getElementById('newAccountDesc').value.trim();
        const type = document.getElementById('newAccountType').value;
        const balance = parseFloat(document.getElementById('newAccountBalance').value) || 0;
        const riskBtn = document.querySelector('.new-risk-btn.bg-blue-600');
        const risk = riskBtn?.dataset.riskNew || 'moderate';

        const result = await api('/accounts', {
            method: 'POST',
            body: {
                name, description: desc, account_type: type,
                starting_balance: balance, risk_level: risk
            }
        });

        if (result.id) {
            document.getElementById('createAccountModal').classList.add('hidden');
            document.getElementById('newAccountName').value = '';
            document.getElementById('newAccountDesc').value = '';
            document.getElementById('newAccountBalance').value = '0';
            showToast(`Created "${result.name}"`);
            await loadAccounts();
        } else {
            showToast(result.detail || result.error || 'Failed to create', 'error');
        }
    }

    function renderWallets() {
        const container = document.getElementById('walletList');
        if (state.wallets.length === 0) {
            container.innerHTML = '<div class="text-center text-slate-500 py-8">No wallets configured</div>';
            return;
        }

        container.innerHTML = state.wallets.map(w => `
            <div class="bg-navy-700/50 rounded-xl p-4" data-address="${w.address}">
                <div class="flex items-center justify-between mb-3">
                    <div>
                        <div class="font-medium">${w.label || w.address.slice(0, 10) + '...'}</div>
                        <div class="text-xs text-slate-400 font-mono">${w.address.slice(0, 8)}...${w.address.slice(-6)}</div>
                    </div>
                    <div class="text-right">
                        <div class="text-sm">$${w.exposure.toFixed(2)} / $${w.budget.toFixed(2)}</div>
                        <div class="text-xs text-slate-400">${w.usage_pct.toFixed(0)}% used</div>
                    </div>
                </div>
                <div class="h-2 bg-navy-600 rounded-full overflow-hidden mb-3">
                    <div class="h-full bg-blue-500 rounded-full" style="width: ${Math.min(w.usage_pct, 100)}%"></div>
                </div>
                <div class="flex items-center justify-between">
                    <span class="text-sm text-slate-400">Allocation</span>
                    <div class="flex items-center gap-2">
                        <input type="range" min="0" max="100" value="${w.allocation_pct || 33}"
                               class="w-24" data-wallet="${w.address}">
                        <span class="text-sm w-10 text-right">${w.allocation_pct || 33}%</span>
                    </div>
                </div>
            </div>
        `).join('');

        // Add allocation slider listeners
        container.querySelectorAll('input[type="range"]').forEach(slider => {
            slider.addEventListener('input', (e) => {
                e.target.nextElementSibling.textContent = `${e.target.value}%`;
            });
            slider.addEventListener('change', async (e) => {
                const address = e.target.dataset.wallet;
                const value = parseFloat(e.target.value);

                if (state.isOperator) {
                    // Operators apply directly
                    await api(`/wallets/${address}/allocation`, {
                        method: 'PATCH',
                        body: { allocation_pct: value }
                    });
                } else {
                    // Viewers queue for review
                    queueChange('wallet_allocation', {
                        address: address,
                        allocation_pct: value
                    });
                }
            });
        });
    }

    function renderPositions() {
        const container = document.getElementById('positionsList');
        if (state.positions.length === 0) {
            container.innerHTML = '<div class="text-center text-slate-500 py-8">No open positions</div>';
            return;
        }

        container.innerHTML = state.positions.map(p => `
            <div class="bg-navy-700/50 rounded-xl p-4">
                <div class="flex items-center justify-between mb-2">
                    <div class="font-medium text-sm">${p.market_short}</div>
                    <span class="px-2 py-0.5 rounded text-xs font-medium ${p.side === 'BUY' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}">
                        ${p.side}
                    </span>
                </div>
                <div class="flex items-center justify-between text-sm">
                    <span class="text-slate-400">${p.wallet}</span>
                    <span>${p.outcome || ''}</span>
                </div>
                <div class="flex items-center justify-between mt-2 text-sm">
                    <span class="text-slate-400">${p.shares.toFixed(2)} shares @ ${p.avg_cost.toFixed(4)}</span>
                    <span class="font-medium">$${p.usdc_spent.toFixed(2)}</span>
                </div>
            </div>
        `).join('');
    }

    function renderRecentTrades() {
        const container = document.getElementById('recentTrades');
        if (state.trades.length === 0) {
            container.innerHTML = '<div class="text-center text-slate-500 py-8">No trades yet</div>';
            return;
        }

        container.innerHTML = state.trades.slice(0, 10).map(t => `
            <div class="bg-navy-700/50 rounded-xl p-3 flex items-center justify-between">
                <div class="flex items-center gap-3">
                    <div class="w-8 h-8 rounded-lg flex items-center justify-center ${
                        t.side === 'BUY' ? 'bg-emerald-500/20' :
                        t.side === 'SELL' ? 'bg-amber-500/20' :
                        t.side === 'RESOLVE' && t.price > 0 ? 'bg-emerald-500/20' : 'bg-red-500/20'
                    }">
                        <span class="text-xs font-medium ${
                            t.side === 'BUY' ? 'text-emerald-400' :
                            t.side === 'SELL' ? 'text-amber-400' :
                            t.side === 'RESOLVE' && t.price > 0 ? 'text-emerald-400' : 'text-red-400'
                        }">${t.side.slice(0, 1)}</span>
                    </div>
                    <div>
                        <div class="text-sm font-medium">${t.market_id}</div>
                        <div class="text-xs text-slate-400">${t.time}</div>
                    </div>
                </div>
                <div class="text-right">
                    <div class="text-sm font-medium">$${t.usdc_amount.toFixed(2)}</div>
                    <div class="text-xs text-slate-400">${t.shares.toFixed(2)} @ ${t.price.toFixed(4)}</div>
                </div>
            </div>
        `).join('');
    }

    function renderPendingTrades() {
        const container = document.getElementById('pendingTrades');
        const section = document.getElementById('pendingSection');
        const countEl = document.getElementById('pendingCount');

        if (state.pendingTrades.length === 0) {
            section.classList.add('hidden');
            return;
        }

        section.classList.remove('hidden');
        countEl.textContent = state.pendingTrades.length;

        container.innerHTML = state.pendingTrades.map(t => `
            <div class="bg-amber-500/10 border border-amber-500/30 rounded-xl p-3">
                <div class="flex items-center justify-between mb-2">
                    <span class="font-medium text-sm">${t.market_id.slice(0, 12)}...</span>
                    <span class="text-amber-400 text-sm">$${t.usdc_amount.toFixed(2)}</span>
                </div>
                <div class="flex gap-2">
                    <button class="approve-btn flex-1 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium" data-id="${t.id}">
                        Approve
                    </button>
                    <button class="reject-btn flex-1 py-2 bg-red-600/20 hover:bg-red-600/30 text-red-400 rounded-lg text-sm font-medium" data-id="${t.id}">
                        Reject
                    </button>
                </div>
            </div>
        `).join('');

        // Add button listeners
        container.querySelectorAll('.approve-btn').forEach(btn => {
            btn.addEventListener('click', () => handleTradeDecision(btn.dataset.id, 'approve'));
        });
        container.querySelectorAll('.reject-btn').forEach(btn => {
            btn.addEventListener('click', () => handleTradeDecision(btn.dataset.id, 'reject'));
        });
    }

    function renderMyRequests() {
        const container = document.getElementById('myRequestsList');
        const countEl = document.getElementById('myRequestsCount');

        if (!container) return;

        const pending = state.myRequests.filter(r => r.status === 'pending');
        countEl.textContent = pending.length;

        if (state.myRequests.length === 0) {
            container.innerHTML = '<div class="text-center text-slate-500 py-4 text-sm">No requests yet</div>';
            return;
        }

        container.innerHTML = state.myRequests.slice(0, 10).map(r => {
            const statusColors = {
                pending: 'bg-amber-500/20 text-amber-400',
                approved: 'bg-emerald-500/20 text-emerald-400',
                denied: 'bg-red-500/20 text-red-400'
            };
            return `
                <div class="bg-navy-800/50 rounded-lg p-3">
                    <div class="flex items-center justify-between mb-1">
                        <span class="text-sm font-medium">${formatRequestType(r.request_type)}</span>
                        <span class="px-2 py-0.5 rounded text-xs ${statusColors[r.status]}">${r.status}</span>
                    </div>
                    <div class="text-xs text-slate-400">${formatDate(r.created_at)}</div>
                    ${r.review_note ? `<div class="text-xs text-slate-500 mt-1">"${r.review_note}"</div>` : ''}
                </div>
            `;
        }).join('');
    }

    function renderAdminRequests() {
        const container = document.getElementById('adminRequestsList');
        if (!container) return;

        if (state.pendingRequests.length === 0) {
            container.innerHTML = '<div class="text-center text-slate-500 py-4 text-sm">No pending requests</div>';
            return;
        }

        container.innerHTML = state.pendingRequests.map(r => `
            <div class="bg-blue-500/10 border border-blue-500/30 rounded-xl p-4">
                <div class="flex items-center justify-between mb-2">
                    <div>
                        <span class="font-medium">${formatRequestType(r.request_type)}</span>
                        <span class="text-xs text-slate-400 ml-2">by ${r.submitted_by}</span>
                    </div>
                    <span class="text-xs text-slate-400">${formatDate(r.created_at)}</span>
                </div>
                <div class="text-sm text-slate-300 mb-2">
                    ${r.reason || 'No reason provided'}
                </div>
                <div class="text-xs text-slate-400 mb-3">
                    Requested: <code class="bg-navy-800 px-1 rounded">${r.requested_value}</code>
                </div>
                <div class="flex gap-2">
                    <button class="request-approve-btn flex-1 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium" data-id="${r.id}">
                        Approve & Apply
                    </button>
                    <button class="request-deny-btn flex-1 py-2 bg-red-600/20 hover:bg-red-600/30 text-red-400 rounded-lg text-sm font-medium" data-id="${r.id}">
                        Deny
                    </button>
                </div>
            </div>
        `).join('');

        // Add button listeners
        container.querySelectorAll('.request-approve-btn').forEach(btn => {
            btn.addEventListener('click', () => handleRequestReview(btn.dataset.id, 'approve'));
        });
        container.querySelectorAll('.request-deny-btn').forEach(btn => {
            btn.addEventListener('click', () => handleRequestReview(btn.dataset.id, 'deny'));
        });
    }

    function renderAdminActivity(activity) {
        const container = document.getElementById('adminActivityList');
        if (!container) return;

        if (activity.length === 0) {
            container.innerHTML = '<div class="text-center text-slate-500 py-4 text-sm">No recent activity</div>';
            return;
        }

        container.innerHTML = activity.map(a => `
            <div class="flex items-center justify-between py-2 border-b border-slate-700/50 last:border-0">
                <div>
                    <span class="text-sm">${a.event_type}</span>
                    <span class="text-xs text-slate-400 ml-2">${a.detail || ''}</span>
                </div>
                <span class="text-xs text-slate-500">${formatDate(a.created_at)}</span>
            </div>
        `).join('');
    }

    function renderPendingChanges() {
        const container = document.getElementById('pendingChanges');
        if (!container) return;

        const changes = Object.entries(state.pendingChanges);
        if (changes.length === 0) {
            container.innerHTML = '<div class="text-sm text-slate-400">No changes queued</div>';
            document.getElementById('submitRequestBtn')?.setAttribute('disabled', 'true');
            return;
        }

        document.getElementById('submitRequestBtn')?.removeAttribute('disabled');
        container.innerHTML = changes.map(([key, value]) => `
            <div class="flex items-center justify-between bg-navy-800/50 rounded-lg px-3 py-2">
                <span class="text-sm">${formatRequestType(key)}</span>
                <button class="remove-change text-red-400 text-xs hover:text-red-300" data-key="${key}">Remove</button>
            </div>
        `).join('');

        container.querySelectorAll('.remove-change').forEach(btn => {
            btn.addEventListener('click', () => {
                delete state.pendingChanges[btn.dataset.key];
                renderPendingChanges();
            });
        });
    }

    // ── Futures Rendering ─────────────────────────────────────────────────────
    function renderFuturesStats() {
        const stats = state.futuresStats;
        document.getElementById('futuresBalance').textContent = `$${(stats.balance || 0).toFixed(2)}`;
        document.getElementById('futuresMargin').textContent = `$${(stats.margin_used || 0).toFixed(2)}`;
        document.getElementById('futuresAvailable').textContent = `$${(stats.margin_available || 0).toFixed(2)}`;

        const pnl = (stats.total_pnl || 0) + (stats.unrealized_pnl || 0);
        const pnlEl = document.getElementById('futuresPnl');
        pnlEl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
        pnlEl.className = pnl >= 0 ? 'text-sm font-semibold text-emerald-400' : 'text-sm font-semibold text-red-400';

        document.getElementById('futuresTrades').textContent = stats.total_trades || 0;
        document.getElementById('futuresPositionCount').textContent = `${stats.open_positions || 0} positions`;
    }

    function renderFuturesPositions() {
        const container = document.getElementById('futuresPositionsList');
        if (!container) return;

        if (state.futuresPositions.length === 0) {
            container.innerHTML = '<div class="text-center text-slate-500 py-6 text-sm">No open futures positions</div>';
            return;
        }

        container.innerHTML = state.futuresPositions.map(p => {
            const pnl = p.unrealized_pnl || 0;
            const pnlPct = p.pnl_pct || 0;
            const pnlColor = pnl >= 0 ? 'text-emerald-400' : 'text-red-400';
            const sideColor = p.side === 'LONG' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400';

            return `
            <div class="bg-navy-700/50 rounded-xl p-4">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-2">
                        <span class="text-lg font-bold text-amber-400">${p.symbol}</span>
                        <span class="px-2 py-0.5 rounded text-xs font-medium ${sideColor}">${p.side}</span>
                        <span class="text-xs text-slate-500">${p.leverage}x</span>
                    </div>
                    <div class="text-right">
                        <div class="${pnlColor} font-semibold">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</div>
                        <div class="text-xs text-slate-400">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%</div>
                    </div>
                </div>
                <div class="grid grid-cols-3 gap-2 text-xs">
                    <div>
                        <span class="text-slate-400">Size</span>
                        <div class="font-medium">${p.size.toFixed(4)} BTC</div>
                    </div>
                    <div>
                        <span class="text-slate-400">Entry</span>
                        <div class="font-medium">$${p.entry_price.toFixed(2)}</div>
                    </div>
                    <div>
                        <span class="text-slate-400">Margin</span>
                        <div class="font-medium">$${p.margin_used.toFixed(2)}</div>
                    </div>
                </div>
                <div class="flex items-center justify-between mt-2 pt-2 border-t border-slate-700/50">
                    <span class="text-xs text-slate-500">Liq: $${(p.liquidation_price || 0).toFixed(2)}</span>
                    <span class="text-xs text-slate-500">${p.trader_short}</span>
                </div>
            </div>`;
        }).join('');
    }

    function renderFuturesTrades() {
        const container = document.getElementById('futuresTradesList');
        if (!container) return;

        if (state.futuresTrades.length === 0) {
            container.innerHTML = '<div class="text-center text-slate-500 py-6 text-sm">No futures trades yet</div>';
            return;
        }

        container.innerHTML = state.futuresTrades.slice(0, 10).map(t => {
            const isClose = t.side.startsWith('CLOSE');
            const isLong = t.side.includes('LONG');
            const pnl = t.realized_pnl || 0;
            const hasPnl = isClose && pnl !== 0;

            const bgColor = isClose
                ? (pnl >= 0 ? 'bg-emerald-500/10' : 'bg-red-500/10')
                : (isLong ? 'bg-emerald-500/10' : 'bg-red-500/10');
            const iconColor = isClose
                ? (pnl >= 0 ? 'text-emerald-400' : 'text-red-400')
                : (isLong ? 'text-emerald-400' : 'text-red-400');

            return `
            <div class="bg-navy-700/50 rounded-xl p-3 flex items-center justify-between">
                <div class="flex items-center gap-3">
                    <div class="w-8 h-8 rounded-lg flex items-center justify-center ${bgColor}">
                        <span class="text-xs font-medium ${iconColor}">${t.side.slice(0, 1)}</span>
                    </div>
                    <div>
                        <div class="text-sm font-medium flex items-center gap-2">
                            ${t.symbol}
                            <span class="text-xs text-slate-500">${t.leverage}x</span>
                        </div>
                        <div class="text-xs text-slate-400">${t.side} | ${t.trader_short}</div>
                    </div>
                </div>
                <div class="text-right">
                    <div class="text-sm font-medium">${t.size.toFixed(4)} BTC</div>
                    ${hasPnl
                        ? `<div class="text-xs ${pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</div>`
                        : `<div class="text-xs text-slate-400">@ $${t.price.toFixed(2)}</div>`
                    }
                </div>
            </div>`;
        }).join('');
    }

    function renderFuturesWallets() {
        const container = document.getElementById('futuresWalletsList');
        if (!container) return;

        if (state.futuresWallets.length === 0) {
            container.innerHTML = '<div class="text-center text-slate-500 py-6 text-sm">No wallets tracked for futures</div>';
            return;
        }

        container.innerHTML = state.futuresWallets.map(w => `
            <div class="bg-navy-700/50 rounded-xl p-3 flex items-center justify-between" data-address="${w.address}">
                <div class="flex items-center gap-3">
                    <div class="w-2 h-2 rounded-full ${w.enabled ? 'bg-amber-500' : 'bg-slate-600'}"></div>
                    <div>
                        <div class="font-medium text-sm">${w.label || 'Unnamed'}</div>
                        <div class="text-xs text-slate-400 font-mono">${w.short}</div>
                    </div>
                </div>
                <button class="remove-futures-wallet touch-btn p-2 text-slate-400 hover:text-red-400 transition-colors" data-address="${w.address}">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                    </svg>
                </button>
            </div>
        `).join('');

        // Bind remove handlers
        container.querySelectorAll('.remove-futures-wallet').forEach(btn => {
            btn.addEventListener('click', async () => {
                const address = btn.dataset.address;
                if (confirm('Remove this wallet from futures tracking?')) {
                    const result = await api(`/futures/wallets/${address}`, { method: 'DELETE' });
                    if (result.success) {
                        showToast('Wallet removed');
                        await loadFuturesWallets();
                    } else {
                        showToast(result.error || 'Failed to remove', 'error');
                    }
                }
            });
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────────
    function formatRequestType(type) {
        const labels = {
            reserve_pct: 'Reserve Percentage',
            reserve_cycling: 'Reserve Cycling',
            risk_level: 'Risk Level',
            trading_profile: 'Trading Profile',
            wallet_allocation: 'Wallet Allocation',
            mode_change: 'Trading Mode',
        };
        return labels[type] || type;
    }

    function formatDate(dateStr) {
        if (!dateStr) return '';
        const d = new Date(dateStr);
        const now = new Date();
        const diff = now - d;

        if (diff < 60000) return 'just now';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
        return d.toLocaleDateString();
    }

    function queueChange(type, value) {
        state.pendingChanges[type] = value;
        renderPendingChanges();

        // Show the submit section
        document.getElementById('submitReviewSection')?.classList.remove('hidden');
    }

    function updateModeIndicator() {
        const indicator = document.getElementById('modeIndicator');
        const liveIndicator = document.getElementById('liveIndicator');

        const labels = {
            paper: 'Paper Mode',
            global: 'LIVE - Global',
            us: 'LIVE - US'
        };

        if (state.mode === 'paper') {
            indicator.textContent = labels.paper;
            indicator.className = 'text-xs text-amber-400';
            if (liveIndicator) {
                liveIndicator.className = 'w-2 h-2 rounded-full bg-amber-400 animate-pulse';
            }
        } else {
            indicator.textContent = labels[state.mode] || state.mode;
            indicator.className = 'text-xs text-emerald-400 font-medium';
            if (liveIndicator) {
                liveIndicator.className = 'w-2 h-2 rounded-full bg-emerald-400 animate-pulse';
            }
        }
    }

    function updateModeButtons() {
        document.querySelectorAll('.mode-btn').forEach(btn => {
            if (btn.dataset.mode === state.mode) {
                btn.classList.add('bg-blue-600');
                btn.classList.remove('bg-navy-600');
            } else {
                btn.classList.remove('bg-blue-600');
                btn.classList.add('bg-navy-600');
            }
        });
    }

    function updateApprovalToggle() {
        const toggle = document.getElementById('approvalToggle');
        if (state.approvalMode === 'manual') {
            toggle.classList.add('active');
        } else {
            toggle.classList.remove('active');
        }
    }

    function updateRiskButtons(level) {
        document.querySelectorAll('.risk-btn').forEach(btn => {
            if (btn.dataset.risk === level) {
                btn.classList.add('bg-blue-600');
                btn.classList.remove('bg-navy-600');
            } else {
                btn.classList.remove('bg-blue-600');
                btn.classList.add('bg-navy-600');
            }
        });
    }

    // ── Event Handlers ────────────────────────────────────────────────────────
    async function handleTradeDecision(tradeId, action) {
        await api('/trade-decision', {
            method: 'POST',
            body: { trade_id: parseInt(tradeId), action }
        });
        await loadPendingTrades();
    }

    async function handleApproveAll() {
        await api('/approve-all', { method: 'POST' });
        await loadPendingTrades();
    }

    async function handleRejectAll() {
        await api('/reject-all', { method: 'POST' });
        await loadPendingTrades();
    }

    async function handleModeChange(mode) {
        if (state.isOperator) {
            const result = await api('/mode', {
                method: 'POST',
                body: { mode }
            });
            if (result.mode) {
                state.mode = result.mode;
                updateModeIndicator();
                updateModeButtons();
            }
        } else {
            queueChange('mode_change', mode);
        }
    }

    async function handleApprovalToggle() {
        if (!state.isOperator) return;

        const newMode = state.approvalMode === 'auto' ? 'manual' : 'auto';
        const result = await api('/approval-mode', {
            method: 'POST',
            body: { mode: newMode }
        });
        if (result.mode) {
            state.approvalMode = result.mode;
            updateApprovalToggle();
        }
    }

    async function handleRiskChange(level) {
        if (state.isOperator) {
            await api(`/accounts/${state.activeAccountId}/profile`, {
                method: 'PATCH',
                body: { risk_level: level }
            });
            await loadProfile();
        } else {
            queueChange('risk_level', level);
        }
    }

    async function handleReserveChange(pct) {
        if (state.isOperator) {
            await api(`/accounts/${state.activeAccountId}/reserve`, {
                method: 'PATCH',
                body: { reserve_pct: pct }
            });
            await loadReserve();
        } else {
            queueChange('reserve_pct', pct);
        }
    }

    async function handleCyclingToggle() {
        const toggle = document.getElementById('cyclingToggle');
        const enabled = !toggle.classList.contains('active');

        if (state.isOperator) {
            await api(`/accounts/${state.activeAccountId}/reserve`, {
                method: 'PATCH',
                body: { cycling_enabled: enabled }
            });
            await loadReserve();
        } else {
            queueChange('reserve_cycling', {
                enabled: enabled,
                schedule: document.querySelector('.schedule-btn.bg-blue-600')?.dataset.schedule || 'daily',
                cycle_pct: parseInt(document.getElementById('cycleSlider').value)
            });
        }
    }

    async function handleScheduleChange(schedule) {
        if (state.isOperator) {
            await api(`/accounts/${state.activeAccountId}/reserve`, {
                method: 'PATCH',
                body: { cycle_schedule: schedule }
            });
            await loadReserve();
        }
    }

    async function handleCyclePctChange(pct) {
        if (state.isOperator) {
            await api(`/accounts/${state.activeAccountId}/reserve`, {
                method: 'PATCH',
                body: { cycle_pct: pct }
            });
        }
    }

    async function handleRequestReview(requestId, action) {
        const result = await api(`/settings-requests/${requestId}/review`, {
            method: 'POST',
            body: { action, note: '' }
        });

        if (result.success) {
            await loadAdminData();
            showToast(result.message);
        }
    }

    async function handleSubmitRequest() {
        const changes = Object.entries(state.pendingChanges);
        if (changes.length === 0) return;

        const reason = document.getElementById('requestReason')?.value || '';

        for (const [type, value] of changes) {
            await api('/settings-requests', {
                method: 'POST',
                body: {
                    request_type: type,
                    category: getCategoryForType(type),
                    requested_value: JSON.stringify(value),
                    reason: reason,
                    account_id: state.activeAccountId,
                }
            });
        }

        // Clear pending changes
        state.pendingChanges = {};
        document.getElementById('requestReason').value = '';
        renderPendingChanges();
        await loadMyRequests();

        showToast('Request submitted for admin review');
    }

    function getCategoryForType(type) {
        if (type.includes('reserve')) return 'reserve';
        if (type.includes('wallet')) return 'wallet';
        if (type.includes('risk') || type.includes('trade')) return 'trading';
        return 'general';
    }

    function showToast(message, type = 'success') {
        // Simple toast notification
        const toast = document.createElement('div');
        const bgColor = type === 'error' ? 'bg-red-600' : 'bg-emerald-600';
        toast.className = `fixed bottom-24 left-4 right-4 ${bgColor} text-white px-4 py-3 rounded-xl text-center text-sm font-medium z-50`;
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }

    async function handleLogout() {
        await api('/logout', { method: 'POST' });
        window.location.href = '/login';
    }

    function switchTab(tabName) {
        state.activeTab = tabName;

        // Update tab buttons
        document.querySelectorAll('.tab-btn').forEach(btn => {
            if (btn.dataset.tab === tabName) {
                btn.classList.add('text-blue-400');
                btn.classList.remove('text-slate-400');
            } else {
                btn.classList.remove('text-blue-400');
                btn.classList.add('text-slate-400');
            }
        });

        // Update tab indicator
        const visibleTabs = Array.from(document.querySelectorAll('.tab-btn:not(.hidden)'));
        const idx = visibleTabs.findIndex(t => t.dataset.tab === tabName);
        const indicator = document.getElementById('tabIndicator');
        if (idx >= 0) {
            indicator.style.transform = `translateX(${idx * 100}%)`;
        }

        // Show/hide panels
        document.querySelectorAll('.tab-panel').forEach(panel => {
            panel.classList.add('hidden');
        });
        document.getElementById(`tab-${tabName}`)?.classList.remove('hidden');
    }

    function switchNav(navName) {
        const navToTab = {
            dashboard: 'dashboard',
            accounts: 'accounts',
            wallets: 'wallets',
            settings: 'settings'
        };

        switchTab(navToTab[navName] || 'dashboard');

        // Load accounts data when switching to accounts tab
        if (navName === 'accounts') {
            loadAccounts();
        }

        document.querySelectorAll('.nav-btn').forEach(btn => {
            if (btn.dataset.nav === navName) {
                btn.classList.add('text-blue-400');
                btn.classList.remove('text-slate-400');
            } else {
                btn.classList.remove('text-blue-400');
                btn.classList.add('text-slate-400');
            }
        });
    }

    // ── Initialization ────────────────────────────────────────────────────────
    function setupEventListeners() {
        // Refresh button
        document.getElementById('refreshBtn').addEventListener('click', refreshAll);

        // Tab buttons
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => switchTab(btn.dataset.tab));
        });

        // Bottom nav
        document.querySelectorAll('.nav-btn').forEach(btn => {
            btn.addEventListener('click', () => switchNav(btn.dataset.nav));
        });

        // Mode buttons
        document.querySelectorAll('.mode-btn').forEach(btn => {
            btn.addEventListener('click', () => handleModeChange(btn.dataset.mode));
        });

        // Approval toggle
        document.getElementById('approvalToggle').addEventListener('click', handleApprovalToggle);

        // Risk buttons
        document.querySelectorAll('.risk-btn').forEach(btn => {
            btn.addEventListener('click', () => handleRiskChange(btn.dataset.risk));
        });

        // Reserve slider
        const reserveSlider = document.getElementById('reserveSlider');
        reserveSlider.addEventListener('input', (e) => {
            document.getElementById('reservePctValue').textContent = `${e.target.value}%`;
        });
        reserveSlider.addEventListener('change', (e) => {
            handleReserveChange(parseFloat(e.target.value));
        });

        // Cycling toggle
        document.getElementById('cyclingToggle').addEventListener('click', handleCyclingToggle);

        // Schedule buttons
        document.querySelectorAll('.schedule-btn').forEach(btn => {
            btn.addEventListener('click', () => handleScheduleChange(btn.dataset.schedule));
        });

        // Cycle percentage slider
        const cycleSlider = document.getElementById('cycleSlider');
        cycleSlider.addEventListener('input', (e) => {
            document.getElementById('cyclePctValue').textContent = `${e.target.value}%`;
        });
        cycleSlider.addEventListener('change', (e) => {
            handleCyclePctChange(parseFloat(e.target.value));
        });

        // Approve/Reject all trades
        document.getElementById('approveAllBtn')?.addEventListener('click', handleApproveAll);
        document.getElementById('rejectAllBtn')?.addEventListener('click', handleRejectAll);

        // Admin approve/reject all trades
        document.getElementById('adminApproveAllTrades')?.addEventListener('click', handleApproveAll);
        document.getElementById('adminRejectAllTrades')?.addEventListener('click', handleRejectAll);

        // Refresh requests (admin)
        document.getElementById('refreshRequestsBtn')?.addEventListener('click', loadAdminData);

        // Submit request (viewer)
        document.getElementById('submitRequestBtn')?.addEventListener('click', handleSubmitRequest);

        // Normalize allocations
        document.getElementById('normalizeBtn')?.addEventListener('click', async () => {
            if (state.isOperator) {
                await api('/wallets/allocations/normalize', { method: 'POST' });
                await loadWallets();
            }
        });

        // Logout
        document.getElementById('logoutBtn').addEventListener('click', handleLogout);

        // Activation modal buttons
        document.getElementById('activationApproveBtn')?.addEventListener('click', handleActivationApprove);
        document.getElementById('activationDenyBtn')?.addEventListener('click', handleActivationDeny);

        // Go Live button (admin trading status card)
        document.getElementById('goLiveBtn')?.addEventListener('click', handleGoLiveClick);

        // Environment Variables modal
        document.getElementById('addEnvVarBtn')?.addEventListener('click', () => showEnvVarModal('', false));
        document.getElementById('closeEnvVarBtn')?.addEventListener('click', hideEnvVarModal);
        document.getElementById('cancelEnvVarBtn')?.addEventListener('click', hideEnvVarModal);
        document.getElementById('saveEnvVarBtn')?.addEventListener('click', saveEnvVar);
        document.getElementById('toggleEnvVarVisibility')?.addEventListener('click', toggleEnvVarVisibility);

        // Update description when key changes
        document.getElementById('envVarKey')?.addEventListener('change', (e) => {
            const key = e.target.value;
            const config = ENV_VAR_CONFIG[key];
            const descEl = document.getElementById('envVarDescription');
            const descText = document.getElementById('envVarDescText');
            if (key && config) {
                descEl.classList.remove('hidden');
                descText.textContent = config.description;
            } else {
                descEl.classList.add('hidden');
            }
        });

        // Create Account modal
        document.getElementById('createAccountBtn')?.addEventListener('click', () => {
            document.getElementById('createAccountModal').classList.remove('hidden');
        });
        document.getElementById('cancelCreateAccount')?.addEventListener('click', () => {
            document.getElementById('createAccountModal').classList.add('hidden');
        });
        document.getElementById('confirmCreateAccount')?.addEventListener('click', handleCreateAccount);

        // New account risk level buttons
        document.querySelectorAll('.new-risk-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.new-risk-btn').forEach(b => {
                    b.classList.remove('bg-blue-600', 'text-white');
                    b.classList.add('bg-navy-600', 'text-slate-300');
                });
                btn.classList.remove('bg-navy-600', 'text-slate-300');
                btn.classList.add('bg-blue-600', 'text-white');
            });
        });

        // Close account detail modal on backdrop click
        document.getElementById('accountDetailModal')?.addEventListener('click', (e) => {
            if (e.target === e.currentTarget) e.currentTarget.classList.add('hidden');
        });
        document.getElementById('createAccountModal')?.addEventListener('click', (e) => {
            if (e.target === e.currentTarget) e.currentTarget.classList.add('hidden');
        });

        // ── Futures Event Handlers ──
        // Add futures wallet modal
        document.getElementById('addFuturesWalletBtn')?.addEventListener('click', () => {
            document.getElementById('addFuturesWalletModal').classList.remove('hidden');
        });

        document.getElementById('cancelAddFuturesWallet')?.addEventListener('click', () => {
            document.getElementById('addFuturesWalletModal').classList.add('hidden');
            document.getElementById('futuresWalletAddress').value = '';
            document.getElementById('futuresWalletLabel').value = '';
        });

        document.getElementById('confirmAddFuturesWallet')?.addEventListener('click', async () => {
            const address = document.getElementById('futuresWalletAddress').value.trim();
            const label = document.getElementById('futuresWalletLabel').value.trim();

            if (!address) {
                showToast('Enter a wallet address', 'error');
                return;
            }

            const result = await api('/futures/wallets', {
                method: 'POST',
                body: { address, label }
            });

            if (result.success) {
                document.getElementById('addFuturesWalletModal').classList.add('hidden');
                document.getElementById('futuresWalletAddress').value = '';
                document.getElementById('futuresWalletLabel').value = '';
                showToast('Wallet added to futures tracking');
                await loadFuturesWallets();
            } else {
                showToast(result.error || 'Failed to add wallet', 'error');
            }
        });

        // Close futures wallet modal on backdrop click
        document.getElementById('addFuturesWalletModal')?.addEventListener('click', (e) => {
            if (e.target === e.currentTarget) {
                e.currentTarget.classList.add('hidden');
                document.getElementById('futuresWalletAddress').value = '';
                document.getElementById('futuresWalletLabel').value = '';
            }
        });

        // Reset futures account (admin only)
        document.getElementById('resetFuturesBtn')?.addEventListener('click', async () => {
            if (!confirm('Reset futures paper account? This will clear all positions and trades.')) return;

            const result = await api('/futures/reset', { method: 'POST' });
            if (result.success) {
                showToast(result.message);
                await loadFuturesData();
            } else {
                showToast(result.error || 'Reset failed', 'error');
            }
        });

        // Pull to refresh is handled by swipe.js PullToRefresh class
    }

    // ── Start ─────────────────────────────────────────────────────────────────
    async function init() {
        setupEventListeners();
        await loadUser();
        await loadAccounts();
        await refreshAll();

        // Check if activation prompt should be shown (operators only)
        await checkActivationPrompt();

        // Auto-refresh every 30 seconds
        setInterval(refreshAll, 30000);
    }

    // Export for swipe.js
    window.refreshAll = refreshAll;

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
