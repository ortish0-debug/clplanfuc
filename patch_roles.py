"""Пакетная замена кнопок — добавление проверок ролей."""

with open("static/index.html", "r", encoding="utf-8") as f:
    content = f.read()

replacements = [
    # ── ЗАЯВКИ НА ОПЛАТУ ──
    (
        '<Button onClick={() => setShowModal(true)}>+ Создать заявку</Button>',
        "{canWrite('payment-requests') && <Button onClick={() => setShowModal(true)}>+ Создать заявку</Button>}"
    ),
    (
        '<Button onClick={() => setShowModal(true)}>Создать первую заявку</Button>',
        "{canWrite('payment-requests') && <Button onClick={() => setShowModal(true)}>Создать первую заявку</Button>}"
    ),
    # ── СОТРУДНИКИ ──
    (
        "{tab === 'employees' && <Button onClick={() => setShowModal(true)}>+ Добавить сотрудника</Button>}",
        "{tab === 'employees' && canWrite('payroll') && <Button onClick={() => setShowModal(true)}>+ Добавить сотрудника</Button>}"
    ),
    (
        '<Button onClick={() => setShowModal(true)}>Добавить первого сотрудника</Button>',
        "{canWrite('payroll') && <Button onClick={() => setShowModal(true)}>Добавить первого сотрудника</Button>}"
    ),
    # ── ОСНОВНЫЕ СРЕДСТВА ──
    (
        '<Button onClick={() => setShowModal(true)}>+ Добавить ОС</Button>',
        "{canWrite('assets') && <Button onClick={() => setShowModal(true)}>+ Добавить ОС</Button>}"
    ),
    (
        '<Button onClick={() => setShowModal(true)}>Добавить первое ОС</Button>',
        "{canWrite('assets') && <Button onClick={() => setShowModal(true)}>Добавить первое ОС</Button>}"
    ),
    # ── ДОКУМЕНТЫ ──
    (
        '<Button onClick={() => setShowModal(true)}>+ Добавить документ</Button>',
        "{canWrite('documents') && <Button onClick={() => setShowModal(true)}>+ Добавить документ</Button>}"
    ),
    (
        '<Button onClick={() => setShowModal(true)}>Загрузить первый документ</Button>',
        "{canWrite('documents') && <Button onClick={() => setShowModal(true)}>Загрузить первый документ</Button>}"
    ),
    # ── ЗАЙМЫ ──
    (
        '<Button onClick={() => setShowModal(true)}>+ Добавить займ</Button>',
        "{canWrite('loans') && <Button onClick={() => setShowModal(true)}>+ Добавить займ</Button>}"
    ),
    (
        '<Button onClick={() => setShowModal(true)}>Добавить первый займ</Button>',
        "{canWrite('loans') && <Button onClick={() => setShowModal(true)}>Добавить первый займ</Button>}"
    ),
    # ── ТРАНЗАКЦИИ ──
    (
        '<Button onClick={() => setShowModal(true)}>+ Добавить</Button>',
        "{canWrite('transactions') && <Button onClick={() => setShowModal(true)}>+ Добавить</Button>}"
    ),
    (
        '<Button onClick={() => setShowModal(true)}>Добавить первую операцию</Button>',
        "{canWrite('transactions') && <Button onClick={() => setShowModal(true)}>Добавить первую операцию</Button>}"
    ),
    # ── КОНТРАГЕНТЫ ──
    (
        '<Button onClick={() => setShowModal(true)}>+ Добавить контрагента</Button>',
        "{canWrite('counterparties') && <Button onClick={() => setShowModal(true)}>+ Добавить контрагента</Button>}"
    ),
    (
        '<Button onClick={() => setShowModal(true)}>Добавить первого контрагента</Button>',
        "{canWrite('counterparties') && <Button onClick={() => setShowModal(true)}>Добавить первого контрагента</Button>}"
    ),
    # ── CRM ──
    (
        '<Button onClick={() => { resetForm(); setShowModal(true); }}>+ Новая сделка</Button>',
        "{canWrite('crm') && <Button onClick={() => { resetForm(); setShowModal(true); }}>+ Новая сделка</Button>}"
    ),
    # ── НАЧИСЛЕНИЯ (документ) ──
    (
        "{tab === 'documents' && <Button onClick={() => setShowModal(true)}>+ Создать документ</Button>}",
        "{tab === 'documents' && canWrite('accruals') && <Button onClick={() => setShowModal(true)}>+ Создать документ</Button>}"
    ),
    (
        '<Button onClick={() => setShowModal(true)}>Создать первый документ</Button>',
        "{canWrite('accruals') && <Button onClick={() => setShowModal(true)}>Создать первый документ</Button>}"
    ),

    # ── УДАЛЕНИЕ ──
    # Заявки
    (
        "onClick={() => handleDelete(r.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: COLORS.red }}>🗑️</button>",
        "onClick={() => handleDelete(r.id)} style={{ background: 'none', border: 'none', cursor: canDelete() ? 'pointer' : 'default', fontSize: '16px', color: canDelete() ? COLORS.red : COLORS.border, opacity: canDelete() ? 1 : 0.3 }} disabled={!canDelete()}>🗑️</button>"
    ),
    # Сотрудники
    (
        "onClick={() => handleDeleteEmployee(e.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: COLORS.red }}>🗑️</button>",
        "onClick={() => canDelete() && handleDeleteEmployee(e.id)} style={{ background: 'none', border: 'none', cursor: canDelete() ? 'pointer' : 'default', fontSize: '16px', color: canDelete() ? COLORS.red : COLORS.border }}>🗑️</button>"
    ),
    # ОС
    (
        "onClick={() => handleDelete(a.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: COLORS.red }}>🗑️</button>",
        "onClick={() => canDelete() && handleDelete(a.id)} style={{ background: 'none', border: 'none', cursor: canDelete() ? 'pointer' : 'default', fontSize: '16px', color: canDelete() ? COLORS.red : COLORS.border }}>🗑️</button>"
    ),
    # Документы
    (
        "onClick={() => handleDelete(d.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: COLORS.red }}>🗑️</button>",
        "onClick={() => canDelete() && handleDelete(d.id)} style={{ background: 'none', border: 'none', cursor: canDelete() ? 'pointer' : 'default', fontSize: '16px', color: canDelete() ? COLORS.red : COLORS.border }}>🗑️</button>"
    ),
    # Займы
    (
        "onClick={() => handleDelete(loan.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: COLORS.red }}>🗑️</button>",
        "onClick={() => canDelete() && handleDelete(loan.id)} style={{ background: 'none', border: 'none', cursor: canDelete() ? 'pointer' : 'default', fontSize: '16px', color: canDelete() ? COLORS.red : COLORS.border }}>🗑️</button>"
    ),
    # Начисления doc
    (
        "onClick={() => handleDelete(doc.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: COLORS.red }}>🗑️</button>",
        "onClick={() => canDelete() && handleDelete(doc.id)} style={{ background: 'none', border: 'none', cursor: canDelete() ? 'pointer' : 'default', fontSize: '16px', color: canDelete() ? COLORS.red : COLORS.border }}>🗑️</button>"
    ),
    # Транзакции
    (
        'onClick={() => handleDelete(tx.id)} title="Удалить" style={{ background: \'none\', border: \'none\', cursor: \'pointer\', fontSize: \'16px\', color: COLORS.red }}>🗑️</button>',
        "onClick={() => canDelete() && handleDelete(tx.id)} title='Удалить' style={{ background: 'none', border: 'none', cursor: canDelete() ? 'pointer' : 'default', fontSize: '16px', color: canDelete() ? COLORS.red : COLORS.border }}>🗑️</button>"
    ),
    # Контрагенты
    (
        "onClick={() => handleDelete(c.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: COLORS.red }}>🗑️</button>",
        "onClick={() => canDelete() && handleDelete(c.id)} style={{ background: 'none', border: 'none', cursor: canDelete() ? 'pointer' : 'default', fontSize: '16px', color: canDelete() ? COLORS.red : COLORS.border }}>🗑️</button>"
    ),
    # Настройки счёт
    (
        "onClick={() => handleDeleteAccount(acc.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: COLORS.red, fontSize: '16px' }}>🗑️</button>",
        "onClick={() => canSettings() && handleDeleteAccount(acc.id)} style={{ background: 'none', border: 'none', cursor: canSettings() ? 'pointer' : 'default', color: canSettings() ? COLORS.red : COLORS.border, fontSize: '16px' }}>🗑️</button>"
    ),
    # Настройки категория
    (
        "onClick={() => handleDeleteCategory(cat.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: COLORS.red, fontSize: '14px' }}>🗑️</button>",
        "onClick={() => canSettings() && handleDeleteCategory(cat.id)} style={{ background: 'none', border: 'none', cursor: canSettings() ? 'pointer' : 'default', color: canSettings() ? COLORS.red : COLORS.border, fontSize: '14px' }}>🗑️</button>"
    ),
    # CRM
    (
        "onClick={() => handleDelete(deal.id)} title=\"Удалить\" style={{ padding: '3px 8px', borderRadius: '5px', border: `1px solid ${COLORS.redLight}`, background: COLORS.redLight, cursor: 'pointer', fontSize: '12px', color: COLORS.red }}>✕</button>",
        "onClick={() => canDelete() && handleDelete(deal.id)} title='Удалить' style={{ padding: '3px 8px', borderRadius: '5px', border: `1px solid ${COLORS.redLight}`, background: canDelete() ? COLORS.redLight : COLORS.grayLight, cursor: canDelete() ? 'pointer' : 'default', fontSize: '12px', color: canDelete() ? COLORS.red : COLORS.border }}>✕</button>"
    ),

    # ── ОДОБРЕНИЕ ЗАЯВОК ──
    (
        "onClick={() => handleApprove(r.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', marginRight: '8px', color: COLORS.green }}>✅</button>",
        "onClick={() => canApprove() && handleApprove(r.id)} style={{ background: 'none', border: 'none', cursor: canApprove() ? 'pointer' : 'default', fontSize: '16px', marginRight: '8px', color: canApprove() ? COLORS.green : COLORS.border }}>✅</button>"
    ),
    (
        "onClick={() => handleReject(r.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', marginRight: '8px', color: COLORS.red }}>❌</button>",
        "onClick={() => canApprove() && handleReject(r.id)} style={{ background: 'none', border: 'none', cursor: canApprove() ? 'pointer' : 'default', fontSize: '16px', marginRight: '8px', color: canApprove() ? COLORS.red : COLORS.border }}>❌</button>"
    ),

    # ── РЕДАКТИРОВАНИЕ ТРАНЗАКЦИИ ──
    (
        "onClick={() => { setEditingTx(tx); setFormData",
        "onClick={() => { if(canWrite('transactions')){ setEditingTx(tx); setFormData"
    ),
]

changed = 0
not_found = []
for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        changed += 1
    else:
        not_found.append(old[:70])

with open("static/index.html", "w", encoding="utf-8") as f:
    f.write(content)

print(f"Заменено: {changed}/{len(replacements)}")
if not_found:
    print("Не найдено:")
    for s in not_found:
        print(f"  - {s}")
