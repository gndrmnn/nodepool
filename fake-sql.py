import random

print "delete from node;"
print "delete from subnode;"

def node(provider, image, target, i, state):
    print "insert into node values (NULL, '{provider}', '{image}', '{target}', '{image}-{provider}-{i}.slave.openstack.org', '{image}-{provider}-{i}', '48329187d11f4c1e9527ebfeaa2d81a4', NULL, 'fake', {state}, 1409246958);".format(provider=provider, image=image, target=target, i=i, state=state)

for i in range(1000):
    provider = random.choice(['fake-provider%i' % i for i in range(1,9)])
    target = random.choice(['fake-jenkins%i' % i for i in range(1,7)])
    image = random.choice(['bare-precise', 'bare-centos6', 'devstack-precise',
                           'devstack-f20', 'bare-trusty', 'devstack-trusty',
                           'py3k-precise'])
    node(provider, image, target, i, 1)

i+=1
node('fake-provider1', 'devstack-precise', 'fake-jenkins1', i, 2); i+=1
node('fake-provider1', 'devstack-precise', 'fake-jenkins1', i, 2); i+=1
node('fake-provider1', 'devstack-trusty-2-node', 'fake-jenkins1', i, 2); i+=1
node('fake-provider6', 'dsvm-precise-krnl', 'fake-jenkins1', i, 2); i+=1
node('fake-provider1', 'py3k-precise', 'fake-jenkins1', i, 2); i+=1
