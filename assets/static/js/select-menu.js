(() => {
    const enhanced = new WeakMap();
    let active = null, serial = 0;
    function close() {
        if (!active) return;
        active.trigger.setAttribute('aria-expanded', 'false');
        active.trigger.removeAttribute('aria-activedescendant');
        active.menu.remove(); active = null;
    }
    function sync(select) {
        const trigger = enhanced.get(select);
        if (!trigger) return;
        const text = select.selectedOptions[0]?.textContent || '';
        if (trigger.textContent !== text) trigger.textContent = text;
        trigger.disabled = select.disabled;
    }
    function highlight(index) {
        if (!active || !Number.isInteger(index)) return;
        const items = active.items;
        if (index < 0 || index >= items.length || items[index].disabled) return;
        active.index = index;
        items.forEach((item, i) => item.classList.toggle('is-highlighted', i === index));
        active.trigger.setAttribute('aria-activedescendant', items[index].id);
        items[index].scrollIntoView({block:'nearest'});
    }
    function choose(index) {
        if (!active || !active.items[index] || active.items[index].disabled || !active.select.isConnected) return;
        const {select, trigger} = active;
        const changed = select.selectedIndex !== index;
        select.selectedIndex = index;
        sync(select); close(); trigger.focus();
        if (changed) {
            select.dispatchEvent(new Event('input', {bubbles:true}));
            select.dispatchEvent(new Event('change', {bubbles:true}));
        }
    }
    function open(select, trigger) {
        close(); sync(select);
        if (select.disabled) return;
        const menu = document.createElement('div');
        menu.className = 'select-menu'; menu.id = trigger.getAttribute('aria-controls');
        menu.setAttribute('role', 'listbox');
        menu.setAttribute('aria-label', trigger.getAttribute('aria-label'));
        const items = Array.from(select.options, (option, index) => {
            const item = document.createElement('button');
            item.type = 'button'; item.className = 'select-option';
            item.id = `${menu.id}-${index}`; item.tabIndex = -1;
            item.setAttribute('role', 'option');
            item.setAttribute('aria-selected', String(option.selected));
            item.disabled = option.disabled || option.parentElement.disabled === true;
            item.textContent = option.textContent;
            item.addEventListener('mousedown', e => e.preventDefault());
            item.addEventListener('click', () => choose(index));
            menu.append(item); return item;
        });
        document.body.append(menu);
        const rect = trigger.getBoundingClientRect();
        const width = Math.min(Math.max(rect.width, 160), innerWidth - 16);
        menu.style.width = `${width}px`;
        menu.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - width - 8))}px`;
        const below = innerHeight - rect.bottom - 14, above = rect.top - 14;
        const down = below >= Math.min(260, menu.scrollHeight) || below >= above;
        const maxHeight = Math.max(60, Math.min(300, down ? below : above));
        menu.style.maxHeight = `${maxHeight}px`;
        menu.style.top = `${down ? rect.bottom + 6 : Math.max(8, rect.top - Math.min(menu.scrollHeight, maxHeight) - 6)}px`;
        active = {select, trigger, menu, items, index:select.selectedIndex};
        trigger.setAttribute('aria-expanded','true');
        highlight(items[select.selectedIndex]?.disabled ? items.findIndex(i=>!i.disabled) : select.selectedIndex);
    }
    function enhance(select) {
        if (enhanced.has(select) || select.multiple || select.size > 1) return;
        const shell = document.createElement('div'); shell.className = 'select-shell';
        const trigger = document.createElement('button');
        trigger.type = 'button'; trigger.className = 'select-trigger';
        trigger.setAttribute('role','combobox'); trigger.setAttribute('aria-haspopup','listbox');
        trigger.setAttribute('aria-expanded','false');
        trigger.setAttribute('aria-controls',`select-list-${++serial}`);
        const label = select.getAttribute('aria-label') || select.labels?.[0]?.querySelector('span')?.textContent || select.id || 'Select';
        trigger.setAttribute('aria-label',label.trim());
        select.before(shell); shell.append(select, trigger);
        select.classList.add('select-native'); select.tabIndex = -1; select.setAttribute('aria-hidden','true');
        enhanced.set(select,trigger); sync(select);
        select.addEventListener('change',()=>sync(select));
        trigger.addEventListener('click', e => { e.preventDefault(); active?.trigger === trigger ? close() : open(select,trigger); });
        trigger.addEventListener('keydown', e => {
            if (e.key === 'Tab') { close(); return; }
            if (e.key === 'Escape') { e.preventDefault(); close(); return; }
            if (['ArrowDown','ArrowUp','Home','End','Enter',' '].includes(e.key)) {
                e.preventDefault();
                if (active?.trigger !== trigger) { open(select,trigger); return; }
                if (e.key === 'Enter' || e.key === ' ') { choose(active.index); return; }
                const enabled = active.items.map((item,i)=>item.disabled ? -1 : i).filter(i=>i>=0);
                const offset = enabled.indexOf(active.index);
                const next = e.key === 'Home' ? enabled[0] : e.key === 'End' ? enabled.at(-1) : enabled[Math.max(0,Math.min(enabled.length-1,offset+(e.key === 'ArrowDown' ? 1 : -1)))];
                highlight(next);
            } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
                if (active?.trigger !== trigger) open(select,trigger);
                const index = active?.items.findIndex(i=>!i.disabled && i.textContent.trim().toLowerCase().startsWith(e.key.toLowerCase()));
                if (index >= 0) highlight(index);
            }
        });
    }
    function scan(node) {
        if (node.nodeType !== 1) return;
        if (node.matches('select')) enhance(node);
        node.querySelectorAll('select').forEach(enhance);
    }
    scan(document.documentElement);
    new MutationObserver(records => {
        for (const record of records) {
            const element = record.target.nodeType === 1 ? record.target : record.target.parentElement;
            const select = element?.closest('select');
            if (select) sync(select);
            record.addedNodes.forEach(scan);
        }
        if (active && !active.trigger.isConnected) close();
    }).observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['disabled','selected']});
    document.addEventListener('click', e => { if (active && !active.menu.contains(e.target) && !active.trigger.contains(e.target)) close(); });
    // Passive: a scroll listener cannot cancel scrolling anyway, and saying so
    // lets the compositor scroll without waiting to find out.
    document.addEventListener('scroll', e => { if (active && !active.menu.contains(e.target)) close(); },
                              { capture: true, passive: true });
    window.addEventListener('resize',close);
})();
