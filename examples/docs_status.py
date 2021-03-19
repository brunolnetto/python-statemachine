from statemachine import StateMachine, State

# Responsible for the document status history
class DocumentApprovalMachine(StateMachine):
    on_debt = State('OnDebt', initial=True)
    under_approval = State('UnderApproval')
    approved = State('Approved')
    
    start = on_debt.to(under_approval)
    pause = under_approval.to(on_debt)
    finish = under_approval.to(approved)
    
    reset = approved.to(on_debt) | under_approval.to(on_debt)
    next_step = on_debt.to(under_approval) | under_approval.to(on_debt)
    
    def on_on_debt(self):
        print('Document on hold.')

    def on_under_approval(self):
        
        employee = 0;
        while((employee is not '1') or (employee is not '2')):
            employee = raw_input('Choose an approval employee [1/2]: ');
            
            employee_condition = (employee is not '1') or (employee is not '2')
            if(employee_condition):
                print('The employee option is not available.')
                print('Choose 1 or 2')
                
        print('Please, wait for approval.')

    def on_finish(self):
        print('Document approved!')

        
doc_approval = DocumentApprovalMachine()

"Current state must be the initial one"
print(doc_approval.current_state)

"On debt is the current state"
print("is_on_debt? " + str(doc_approval.is_on_debt))

"The available states are ['on_debt', 'under_approval', 'approved']"
print([s.identifier for s in doc_approval.states])

"Current state is 'under_approval'"
doc_approval.start()

"Under Approval is the current state"
print("is_under_approval? " + str(doc_approval.is_under_approval))
doc_approval.run('pause')

"On debt is the current state"
print("is_on_debt? " + str(doc_approval.is_on_debt))

"Alias for next available step on 'next_step' function handle"
doc_approval.next_step()

"Under Approval is the current state"
print(doc_approval.current_state)

"Alias to reset the state machine"
doc_approval.reset()

"On debt is the current state"
print(doc_approval.current_state)

print(doc_approval)
