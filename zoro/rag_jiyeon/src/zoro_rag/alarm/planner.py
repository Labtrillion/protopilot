from __future__ import annotations

from zoro_rag.schemas import AlarmContext, RetrievalQuery


class RetrievalPlanner:
    def __init__(self, include_sop_review: bool = False):
        self.include_sop_review = include_sop_review

    def plan(self, context: AlarmContext) -> list[RetrievalQuery]:
        queries: list[RetrievalQuery] = []
        all_alarm_ids = [alarm.alarm_id for alarm in context.active_alarms]
        all_variables = [alarm.normalized_tag for alarm in context.active_alarms if alarm.normalized_tag]
        for alarm in context.active_alarms:
            descriptor = " ".join(filter(None, [alarm.alarm_id.replace("_", " "), alarm.normalized_tag, alarm.variable_definition, alarm.equipment]))
            queries.append(RetrievalQuery(intent="ALARM_INTERPRETATION", query=f"{descriptor} alarm definition interpretation", alarm_ids=[alarm.alarm_id], variable_ids=[alarm.normalized_tag] if alarm.normalized_tag else []))
            if alarm.normalized_tag:
                queries.append(RetrievalQuery(intent="VARIABLE_DEFINITION", query=f"{alarm.normalized_tag} {alarm.variable_definition or ''} measurement variable definition units", alarm_ids=[alarm.alarm_id], variable_ids=[alarm.normalized_tag]))
        shared = " ".join(context.affected_equipment + all_variables + context.known_disturbances)
        if context.active_objective:
            objective = context.active_objective
            queries.append(RetrievalQuery(
                intent="OPERATING_OBJECTIVE",
                query=f"{shared} {objective.metric} {objective.direction} {objective.description or ''} operating objective constraints trade-offs",
                alarm_ids=all_alarm_ids, variable_ids=all_variables,
            ))
        for disturbance in context.known_disturbances:  # 평가 모드에서만 채워짐
            queries.append(RetrievalQuery(intent="DISTURBANCE_DEFINITION", query=f"{disturbance} disturbance definition", alarm_ids=all_alarm_ids))
        if self.include_sop_review and all_variables:
            # 관측한 경보 태그로 SOP 대응 단계를 찾는다. 외란 번호(정답)는 쓰지 않는다.
            directions = " ".join(f"{a.normalized_tag} {a.direction or ''} {a.variable_definition or ''}" for a in context.active_alarms if a.normalized_tag)
            queries.append(RetrievalQuery(
                intent="SOP_REVIEW", query=f"SOP alarm response {directions} {' '.join(context.affected_equipment)} control loop setpoint case simulation",
                alarm_ids=all_alarm_ids, variable_ids=all_variables,
            ))
        queries.extend([
            RetrievalQuery(intent="POSSIBLE_CAUSES", query=f"{shared} disturbances possible causes abnormal deviation", alarm_ids=all_alarm_ids, variable_ids=all_variables),
            RetrievalQuery(intent="PROCESS_RELATIONSHIPS", query=f"{shared} process control relationships interactions", alarm_ids=all_alarm_ids, variable_ids=all_variables),
            RetrievalQuery(intent="OPERATING_CONSTRAINTS", query=f"{shared} operating constraints safety limits shutdown override", alarm_ids=all_alarm_ids, variable_ids=all_variables),
            RetrievalQuery(intent="HISTORICAL_EXPERIMENTS", query=f"{shared} case experiment scenario operational response", alarm_ids=all_alarm_ids, variable_ids=all_variables),
        ])
        unique: dict[tuple[str, str], RetrievalQuery] = {}
        for query in queries:
            unique[(query.intent, query.query)] = query
        return list(unique.values())
