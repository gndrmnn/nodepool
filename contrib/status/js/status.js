// Copyright (C) 2017 OpenStack Foundation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
// implied.
//
// See the License for the specific language governing permissions and
// limitations under the License.

/*
 * Nodepool status page functions
 */

var dibImageList = "http://nodepool.openstack.org/dib-image-list.json";

$( document ).ready(function() {
    $.getJSON(dibImageList)
        .done(function( data ) {
            $.each( data, function( i, item ) {
                var state = 'success';
                if (item.state != "ready") {
                    state = 'warning'
                }
                var age = new Date(item.age*1000)
                $("#dibImageListTable").find('tbody').append([
                    '<tr>',
                    '<td><span class="label label-info">' + item.image + '</span></td>',
                    '<td><tt>' + item.id + '</tt></td>',
                    '<td><span class="label label-' + state + '">' + item.state + '</span></td>',
                    '<td>' + $.timeago(age) + '</td>',
                    '<td><a href="http://' + item.builder + '.openstack.org/dib.' + item.image +'.log">' + item.builder + '</a></td>',
                    '</tr>'].join(''));
            });
        });
});

