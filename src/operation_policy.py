"""NetPilot task-category policy and safe user-facing error explanations.

Deliberately dependency-free: status polling must not block explicit user
operations, while every unrecognized future task remains blocking by default.
"""

_BACKGROUND = frozenset({'updates', 'warpstatus', 'autohealth'})


def has_foreground_work(running):
    """Only jobs that mutate the system block strategy selection/installation."""
    return any(str(job) not in _BACKGROUND and not str(job).startswith('zapret_inventory:')
               for job in running)


def humanize_zapret_error(message):
    """Explain known safe-stop failures; preserve original diagnostics elsewhere."""
    text = str(message)
    low = text.casefold()
    if 'name-conflicting zapret service is not in the confirmed inventory' in low:
        return ('В Windows существует служба с именем zapret, но NetPilot не подтвердил, '
                'что она относится к обнаруженной установке. Автозапуск не изменён. '
                'Откройте «Диагностика» и проверьте путь службы; стороннюю службу '
                'нельзя заменять по совпадению имени.')
    if 'transaction-owned service is missing from inventory' in low:
        return ('После создания службы Windows не предоставила её в списке обнаруженных служб. '
                'NetPilot остановил передачу управления и попытался восстановить прежнюю конфигурацию. '
                'Проверьте текущую службу в диагностике; подробный отчёт сохранён в журнале.')
    if 'could not verify selected netpilot service configuration' in low:
        return ('Windows не подтвердила конфигурацию службы NetPilot после её создания. '
                'Изменение автозапуска не считается успешным. Проверьте диагностику и журнал.')
    if 'name-conflicting zapret service is not confirmed' in low:
        return ('Служба zapret обнаружена, но её принадлежность не подтверждена. '
                'Автозапуск не изменён. Проверьте путь службы в диагностике.')
    if 'inventory is incomplete or changed' in low:
        return ('Сведения о Zapret изменились или недоступны. Ничего не изменено: '
                'обновите диагностику и повторите подтверждение.')
    if 'accessdenied' in low and 'zapret' in low:
        return ('Windows запретила чтение данных процесса Zapret. Операция отменена; '
                'подробности доступны в диагностике.')
    return text
